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
