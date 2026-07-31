"""Python analyzer for the AST summarization framework (QB-005B).

This module is a relocation, not a rewrite: `_compressible_body_lines()` and
`_body_line_range()` are moved here unmodified from their original home in
`quor/pipeline/stages/python_ast_summarize.py` (QB-005). Their logic,
docstrings, and behavior are byte-for-byte identical to before this move —
see `docs/design/QB-005A-ast-summarization-design.md` Section 9 (QB-005B)
and `backlog.md`'s QB-005B entry for why this move happened and how it was
verified not to change any observable output.

Public API: `analyze_python(source: str) -> set[int]` — returns the
1-indexed line numbers eligible for compression (function/method bodies).

Fail-open contract (deliberately the OPPOSITE of
`quor/pipeline/extract/registry.py`'s `extract()`): this function does NOT
catch `ast.parse()` failures. A `SyntaxError`/`ValueError` (invalid syntax,
a null byte, ...) propagates straight through to the caller, exactly as it
already did when `python_ast_summarize.py`'s `apply()` called `ast.parse()`
directly. `quor/pipeline/stages/python_ast_summarize.py`'s own module
docstring documents why: only per-line, expected failure modes are caught
locally anywhere in this pipeline; a whole-stage parse failure relies on
`Pipeline.execute()`'s existing, already-tested per-stage fail-open
guarantee (ADR-018) instead of a second, redundant try/except here. Any
future analyzer registered in `quor/pipeline/ast_summarize/registry.py`
should follow this same contract: raise on a genuine parse failure for
*that* language, don't swallow it.
"""

from __future__ import annotations

import ast
import sys

from quor.pipeline.ast_summarize.import_collapse import collapse_import_runs
from quor.pipeline.ast_summarize.import_model import (
    ImportBlockReplacement,
    ImportedName,
    ImportStatement,
)
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import ENTRY_POINT_NAMES, Symbol, SymbolKind


def analyze_python(source: str) -> set[int]:
    """Return the 1-indexed line numbers of Python function/method BODY
    lines eligible for compression.

    Raises SyntaxError/ValueError exactly as `ast.parse()` does on
    unparseable input — not caught here, see module docstring.
    """
    tree = ast.parse(source)
    return _compressible_body_lines(tree)


def _compressible_body_lines(tree: ast.Module) -> set[int]:
    """Return the 1-indexed line numbers that belong to a function/method
    BODY (excluding its own signature, decorators, and docstring) and are
    therefore eligible for compression.

    Only top-level functions/methods are considered independently: once a
    function is selected for body compression, its body is not descended
    into any further, so a nested function's lines are covered by its
    enclosing function's range and are never processed on their own (its
    signature is not specially preserved — a function nested inside
    another function is implementation detail of the outer one).
    """
    lines: set[int] = set()

    def visit_body(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines.update(_body_line_range(stmt))
                # Deliberately do not recurse into stmt.body — any nested
                # def/class is already inside the range just added.
            elif isinstance(stmt, ast.ClassDef):
                visit_body(stmt.body)
            elif isinstance(stmt, ast.If):
                # Conditionally-defined top-level functions/classes, e.g.
                # `if TYPE_CHECKING: ...` or a version-gated definition.
                visit_body(stmt.body)
                visit_body(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                # try/except ImportError fallback definitions.
                visit_body(stmt.body)
                for handler in stmt.handlers:
                    visit_body(handler.body)
                visit_body(stmt.orelse)
                visit_body(stmt.finalbody)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                visit_body(stmt.body)

    visit_body(tree.body)
    return lines


def _body_line_range(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Lines belonging to `node`'s body, excluding its signature/decorators
    and any leading docstring.

    Empty for trivial bodies: a same-line definition (`def f(): return 1`,
    where the body starts on the signature's own line — ContentMask is
    line-based and cannot partially compress a single physical line), or a
    body that is only a docstring.
    """
    if node.end_lineno is None or not node.body:
        return set()

    first_stmt = node.body[0]
    docstring_present = (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
        and isinstance(first_stmt.value.value, str)
    )
    remaining = node.body[1:] if docstring_present else node.body
    if not remaining:
        return set()

    start = remaining[0].lineno
    if start <= node.lineno:
        # Same-line body (`def f(): return 1`): the body shares its line with
        # the signature. ContentMask is line-based, so this line can't be
        # partially compressed without touching the signature — leave it alone.
        return set()

    end = node.end_lineno
    if start > end:
        return set()
    return set(range(start, end + 1))


def extract_symbols_python(source: str) -> list[Symbol]:
    """Return every class/function/method Python declares at module scope
    (including inside `if`/`try`/`with` wrappers — the same conditional-
    definition containers `_compressible_body_lines()` above already
    descends into) or one level inside a class body, in source order.

    Raises SyntaxError/ValueError exactly as `ast.parse()` does on
    unparseable input, same fail-open contract as `analyze_python()` — see
    module docstring.
    """
    tree = ast.parse(source)
    symbols: list[Symbol] = []
    _visit_module_body(tree.body, symbols)
    return symbols


def _visit_module_body(stmts: list[ast.stmt], symbols: list[Symbol]) -> None:
    for stmt in stmts:
        if isinstance(stmt, ast.ClassDef):
            symbols.append(_symbol_for(stmt, "class"))
            _visit_class_body(stmt.body, symbols)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_symbol_for(stmt, "function"))
        elif isinstance(stmt, ast.If):
            _visit_module_body(stmt.body, symbols)
            _visit_module_body(stmt.orelse, symbols)
        elif isinstance(stmt, ast.Try):
            _visit_module_body(stmt.body, symbols)
            for handler in stmt.handlers:
                _visit_module_body(handler.body, symbols)
            _visit_module_body(stmt.orelse, symbols)
            _visit_module_body(stmt.finalbody, symbols)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            _visit_module_body(stmt.body, symbols)


def _visit_class_body(stmts: list[ast.stmt], symbols: list[Symbol]) -> None:
    """Recurse one level into a class body: direct methods, and nested
    classes (recorded as their own `"class"` symbol, then recursed into for
    their own methods) — does not descend into a method's own body, mirroring
    `_compressible_body_lines()`'s identical "no further recursion" rule."""
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_symbol_for(stmt, "method"))
        elif isinstance(stmt, ast.ClassDef):
            symbols.append(_symbol_for(stmt, "class"))
            _visit_class_body(stmt.body, symbols)


def _symbol_for(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, kind: SymbolKind
) -> Symbol:
    name = node.name
    return Symbol(
        name=name,
        kind=kind,
        line=node.lineno,
        is_public=not name.startswith("_"),
        is_entry_point=kind == "function" and name in ENTRY_POINT_NAMES,
    )


def extract_relationships_python(source: str) -> list[Relationship]:
    """Return every deterministic import/inherits/export/calls relationship
    Python's AST makes explicit (QB-067). File-local and unresolved — see
    `relationship_model.py`'s module docstring for why resolution against
    other files is not this function's job.

    - **imports**: every `import`/`from ... import ...` anywhere in the
      file (not just module scope — a local/conditional import is still a
      real dependency fact). A relative `from . import x` / `from ..pkg
      import y` is encoded as leading dots in `target` (`"."`, `"..pkg"`)
      exactly mirroring Python's own relative-import syntax, so the
      orchestrator can count them without re-deriving the grammar. A
      wildcard `from x import *` is skipped entirely — QB-067's own
      "conservative, unambiguous only" resolution mandate rules out a
      binding that could shadow an unknown number of names.
    - **inherits**: every `class Foo(Base, ...):` base that is a plain
      name or dotted-attribute reference (`Base`, `pkg.Base`) — a
      keyword arg (`metaclass=...`), call (`Generic[T]` handled via
      `ast.Subscript`), or other dynamic expression is skipped, not
      guessed. Python has no separate interface/trait syntax, so every
      resolvable base is reported as `inherits`, never
      `implements_interface`/`implements_trait` (a real, documented
      language limitation, not an oversight).
    - **export**: only from a module-level `__all__ = [...]`/`(...)`
      assignment of string constants — Python's one real, syntactic
      export mechanism (PEP 8's "public API" list every tool from
      `pydoc` to `from x import *` already honors). A file with no
      `__all__` reports no exports here; that does not mean nothing is
      importable from it — see `symbol_model.py`'s `Symbol.is_public`
      for the (separate, always-available) leading-underscore signal.
    - **overrides**: not emitted — Python has no syntactic override
      marker (unlike Java's `@Override`/C#'s `override` keyword), and
      guessing from same-name-as-a-base-class-method would require the
      exact cross-file method resolution this module deliberately leaves
      to the orchestrator, applied to every method unconditionally,
      which is precisely the "heuristic that cannot be deterministically
      verified from this file alone" QB-067 rules out.
    - **calls**: only from within a function/method already reported as
      a `Symbol` by `extract_symbols_python()` above (top-level, or one
      level inside a class) — `source` is that function/method's own
      name, matching every emitted call to a real, indexable node. A
      call's callee is recorded only when it is a bare name
      (`target=name`, `qualifier=None`) or a single-level attribute
      access on a bare name (`qualifier.target(...)`, e.g. `self.foo()`/
      `np.array()`) — a deeper chain (`a.b.c()`), a subscript, or a call
      result being called (`f()()`) is skipped, not guessed. A call
      found inside a nested `def`/lambda is still attributed to its
      nearest enclosing top-level/class-method `Symbol`, mirroring
      `_compressible_body_lines()`'s own "nested def is implementation
      detail of the outer one" rule.

    Raises SyntaxError/ValueError exactly as `ast.parse()` does on
    unparseable input, same fail-open contract as `analyze_python()`/
    `extract_symbols_python()` — see module docstring.
    """
    tree = ast.parse(source)
    relationships: list[Relationship] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _add_import_relationships(node, relationships)
        elif isinstance(node, ast.ImportFrom):
            _add_import_from_relationships(node, relationships)
        elif isinstance(node, ast.ClassDef):
            _add_inherits_relationships(node, relationships)

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and _is_dunder_all_target(stmt.targets):
            _add_export_relationships(stmt, relationships)

    _visit_module_body_calls(tree.body, relationships)

    return relationships


def _dotted_name(node: ast.expr) -> str | None:
    """Return `"a.b.c"` for a plain `Name`/`Attribute` chain, or `None` for
    any other expression shape (subscript, call, ...) — never guessed."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _add_import_relationships(node: ast.Import, relationships: list[Relationship]) -> None:
    for alias in node.names:
        local_name = alias.asname or alias.name.split(".")[0]
        relationships.append(
            Relationship(kind="import", source="", target=alias.name, line=node.lineno, qualifier=local_name)
        )


def _add_import_from_relationships(node: ast.ImportFrom, relationships: list[Relationship]) -> None:
    target = "." * node.level + (node.module or "")
    for alias in node.names:
        if alias.name == "*":
            continue
        local_name = alias.asname or alias.name
        relationships.append(
            Relationship(
                kind="import",
                source="",
                target=target,
                line=node.lineno,
                qualifier=local_name,
                origin=alias.name,
            )
        )


def _add_inherits_relationships(node: ast.ClassDef, relationships: list[Relationship]) -> None:
    for base in node.bases:
        base_name = _dotted_name(base)
        if base_name is not None:
            relationships.append(
                Relationship(kind="inherits", source=node.name, target=base_name, line=base.lineno)
            )


def _is_dunder_all_target(targets: list[ast.expr]) -> bool:
    return len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == "__all__"


def _add_export_relationships(stmt: ast.Assign, relationships: list[Relationship]) -> None:
    if not isinstance(stmt.value, (ast.List, ast.Tuple)):
        return
    for element in stmt.value.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            relationships.append(
                Relationship(kind="export", source=element.value, target="", line=stmt.lineno)
            )


def _visit_module_body_calls(stmts: list[ast.stmt], relationships: list[Relationship]) -> None:
    """Mirrors `_visit_module_body()`'s exact dispatch shape (module scope,
    including conditional-definition wrappers), collecting a `calls`
    relationship for each top-level function/method `Symbol` instead of a
    compressible line range."""
    for stmt in stmts:
        if isinstance(stmt, ast.ClassDef):
            _visit_class_body_calls(stmt.body, relationships)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _collect_calls(stmt, stmt.name, relationships)
        elif isinstance(stmt, ast.If):
            _visit_module_body_calls(stmt.body, relationships)
            _visit_module_body_calls(stmt.orelse, relationships)
        elif isinstance(stmt, ast.Try):
            _visit_module_body_calls(stmt.body, relationships)
            for handler in stmt.handlers:
                _visit_module_body_calls(handler.body, relationships)
            _visit_module_body_calls(stmt.orelse, relationships)
            _visit_module_body_calls(stmt.finalbody, relationships)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            _visit_module_body_calls(stmt.body, relationships)


def _visit_class_body_calls(stmts: list[ast.stmt], relationships: list[Relationship]) -> None:
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _collect_calls(stmt, stmt.name, relationships)
        elif isinstance(stmt, ast.ClassDef):
            _visit_class_body_calls(stmt.body, relationships)


def _is_stdlib_module(top_level_name: str) -> bool:
    """True if `top_level_name` (the first dotted segment of an imported
    module, e.g. `"os"` from `"os.path"`) is one of this interpreter's own
    standard-library modules — `sys.stdlib_module_names` (3.10+, always
    available under this project's 3.11+ floor) is an authoritative,
    version-specific ground truth, not a guess or a hardcoded list that
    could drift from the Python version actually running."""
    return top_level_name in sys.stdlib_module_names


def collect_import_statements_python(tree: ast.Module) -> list[ImportStatement]:
    """Return every module-level `import`/`from ... import ...` statement,
    in source order (QB-096). Deliberately top-level only — an import
    conditionally executed inside a function/`try`/`if` block is a different
    kind of fact (it may never run, or run only sometimes) than a module's
    unconditional import list, and collapsing it into the same summary would
    misrepresent which imports are unconditional; mirrors this module's own
    `_compressible_body_lines()` restricting *its* recursion to specific,
    well-understood conditional-definition containers rather than collapsing
    everything indiscriminately.
    """
    statements: list[ImportStatement] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            end = stmt.end_lineno or stmt.lineno
            names = tuple(ImportedName(name=alias.name, alias=alias.asname) for alias in stmt.names)
            statements.append(ImportStatement(line=stmt.lineno, end_line=end, module=None, names=names))
        elif isinstance(stmt, ast.ImportFrom):
            end = stmt.end_lineno or stmt.lineno
            module = "." * stmt.level + (stmt.module or "")
            if len(stmt.names) == 1 and stmt.names[0].name == "*":
                statements.append(
                    ImportStatement(line=stmt.lineno, end_line=end, module=module, is_wildcard=True)
                )
            else:
                names = tuple(ImportedName(name=alias.name, alias=alias.asname) for alias in stmt.names)
                statements.append(ImportStatement(line=stmt.lineno, end_line=end, module=module, names=names))
    return statements


def collapse_imports_python(source: str) -> list[ImportBlockReplacement]:
    """Return the collapsed replacement for every token-cheaper run of
    consecutive, module-level import statements (QB-096) — see
    `import_collapse.py`'s module docstring for the shared run-detection,
    rendering, and cost-gate rules this delegates to.

    Raises SyntaxError/ValueError exactly as `ast.parse()` does on
    unparseable input, same fail-open contract as `analyze_python()` — see
    this module's own docstring.
    """
    tree = ast.parse(source)
    statements = collect_import_statements_python(tree)
    if not statements:
        return []
    return collapse_import_runs(statements, source.split("\n"), comment_prefix="#", stdlib_check=_is_stdlib_module)


def _collect_calls(node: ast.AST, source_name: str, relationships: list[Relationship]) -> None:
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if isinstance(func, ast.Name):
            relationships.append(
                Relationship(kind="calls", source=source_name, target=func.id, line=call.lineno)
            )
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            relationships.append(
                Relationship(
                    kind="calls",
                    source=source_name,
                    target=func.attr,
                    line=call.lineno,
                    qualifier=func.value.id,
                )
            )
