"""Go analyzer for the AST summarization framework (QB-046).

Compresses function/method/func-literal **bodies** to nothing, preserving
everything that describes a file's public surface (imports, `const`/`var`/
`type` declarations, struct/interface bodies, function/method signatures,
doc comments) — the same compression philosophy `python.py`/`javascript.py`/
`typescript.py` already implement, mapped onto Go's AST node shapes.

Uses `tree-sitter` + `tree-sitter-go` (optional dependency, `quor[go]` — its
own extra rather than folded into `quor[javascript]`, per QB-046's own
"each is its own new optional dependency" wording). `tree_sitter`/
`tree_sitter_go` are imported **lazily, inside `analyze_go()`**, not at
module top level — mirrors `javascript.py`'s identical lazy-import
discipline, which is what lets `registry.py` register `"go"`
**unconditionally**.

Public API: `analyze_go(source: str) -> set[int]` — returns the 1-indexed
line numbers eligible for compression. Same return-type contract as
`analyze_python()`/`analyze_javascript()`.

Fail-open contract — identical shape to `javascript.py` (see its module
docstring for the full reasoning): a missing `tree-sitter`/`tree-sitter-go`
dependency is caught here and warns; a genuine parse failure on real Go
source is not caught here and propagates to `Pipeline.execute()`'s
per-stage fail-open (ADR-018).

Go has no classes — a method's receiver (`func (w *Widget) Render() ...`)
makes `method_declaration` its own **top-level** sibling node, not nested
inside a container the way a JS class method is (empirically verified
against the installed `tree-sitter-go` grammar while implementing this
module) — so, unlike `javascript.py`, this module needs no
`_visit_class_body()`-equivalent: `_visit_top_level()` alone finds both
`function_declaration` and `method_declaration` directly.

Reuses `_treesitter_utils.py`'s `collect_error_ranges()`/`add_candidate()`
unmodified in spirit — `add_candidate()`/`statement_block_interior_lines()`
gained an optional `block_type` parameter (default `"statement_block"`,
JS/TS's own node type name, unchanged for existing callers) so this module
can pass Go's own block node type, `"block"`, instead of duplicating that
logic — QB-005D's own "language-agnostic helper" framing for this file
extends here to a language whose grammar names the same concept
differently.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from quor.pipeline.ast_summarize._treesitter_utils import (
    add_candidate,
    collect_error_ranges,
    iter_descendants,
)
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import ENTRY_POINT_NAMES, Symbol, SymbolKind

if TYPE_CHECKING:
    from tree_sitter import Node

# Go's own block node type name (vs. JS/TS's "statement_block") — passed to
# _treesitter_utils.add_candidate()/statement_block_interior_lines().
_BLOCK_TYPE = "block"

# Node types (per tree-sitter-go's grammar, empirically verified against the
# installed grammar version while implementing this module) that have a
# `body` field which may be a `block` eligible for compression: a top-level
# function, and a method (receiver + name + body — its own top-level node,
# not nested inside anything else; see module docstring).
_FUNCTION_LIKE_TYPES = frozenset({"function_declaration", "method_declaration"})


def analyze_go(source: str) -> set[int]:
    """Return the 1-indexed line numbers of Go function/method/func-literal
    BODY lines eligible for compression.

    Returns an empty set (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-go` dependency is not installed. Otherwise
    may raise on a genuine, unrecoverable parser failure — not caught here,
    see module docstring "Fail-open".
    """
    try:
        import tree_sitter
        import tree_sitter_go
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-go is not installed; "
            "install quor[go] to enable Go AST summarization "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return set()

    language = tree_sitter.Language(tree_sitter_go.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))
    root = tree.root_node

    # Fast path: only walk the tree for ERROR/MISSING nodes if tree-sitter
    # actually flagged one anywhere — has_error is a cheap, tree-wide flag.
    error_ranges = collect_error_ranges(root) if root.has_error else []

    lines: set[int] = set()
    _visit_top_level(root, error_ranges, lines)
    return lines


def _visit_top_level(node: Node, error_ranges: list[tuple[int, int]], lines: set[int]) -> None:
    """Walk `node`'s children looking for top-level function/method
    declarations and `var` declarations that assign a `func_literal` value
    to a name.

    Deliberately narrow, mirroring `javascript.py`'s own scope: does NOT
    recurse into `if`/`for`/other block containers, and — since a
    `func_literal` in a `var` block is the only assignable-function shape
    at package scope in Go (a package-level `:=` short declaration is not
    legal Go syntax, only ever valid inside a function body) — there is no
    Go analog of JS's `let`/`var`/`const` triad to consider, only `var`.

    Once a function-like node is selected for body compression, this
    function does not recurse into it any further — a function literal
    nested inside another function's body (a closure, a `go func() {...}
    ()`) is implementation detail of the outer one, mirroring
    `python.py`/`javascript.py`'s identical "no further recursion" rule.
    """
    for child in node.children:
        if child.type in _FUNCTION_LIKE_TYPES:
            add_candidate(child, error_ranges, lines, block_type=_BLOCK_TYPE)
        elif child.type == "var_declaration":
            _visit_var_declaration(child, error_ranges, lines)


def _visit_var_declaration(
    decl_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]
) -> None:
    """For each `var_spec` under a `var_declaration` — either a single
    `var f = func() {...}` (the `var_spec` is `decl_node`'s direct child)
    or a grouped `var (...)` block (each `var_spec` is nested one level
    deeper, inside a `var_spec_list`, per the installed grammar) — compress
    the assigned value's body if that value is a `func_literal`.

    A `var_spec`'s `value` field is itself an `expression_list` (Go allows
    `var a, b = f, g`), not the value node directly — unlike JS's
    `variable_declarator`, whose `value` field *is* the assigned
    expression — so this walks one level deeper than `javascript.py`'s
    `_visit_variable_declaration()` before checking for a `func_literal`.
    """
    for child in decl_node.children:
        if child.type == "var_spec":
            _visit_var_spec(child, error_ranges, lines)
        elif child.type == "var_spec_list":
            for spec in child.children:
                if spec.type == "var_spec":
                    _visit_var_spec(spec, error_ranges, lines)


def _visit_var_spec(spec_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]) -> None:
    value = spec_node.child_by_field_name("value")
    if value is None or value.type != "expression_list":
        return
    for expr in value.children:
        if expr.type == "func_literal":
            add_candidate(expr, error_ranges, lines, block_type=_BLOCK_TYPE)


# Go's own grammar names for a struct/interface type-spec's underlying type
# node — mapped to the Symbol kind each represents (QB-066).
_TYPE_SPEC_KINDS: dict[str, SymbolKind] = {"struct_type": "struct", "interface_type": "interface"}


def extract_symbols_go(source: str) -> list[Symbol]:
    """Return every top-level struct/interface type declaration and
    function/method declaration (QB-066), in source order.

    `is_public` uses Go's own exported-identifier rule: a leading-uppercase
    name is exported (public), a leading-lowercase name is package-private
    — the language's own visibility mechanism, not a Quor convention.

    Returns an empty list (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-go` dependency is not installed. Otherwise
    may raise on a genuine, unrecoverable parser failure — not caught here,
    same fail-open contract as `analyze_go()`."""
    try:
        import tree_sitter
        import tree_sitter_go
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-go is not installed; "
            "install quor[go] to enable Go symbol extraction "
            "(falling back to no symbols for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_go.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    symbols: list[Symbol] = []
    for child in tree.root_node.children:
        if child.type == "type_declaration":
            _add_type_spec_symbols(child, symbols)
        elif child.type in _FUNCTION_LIKE_TYPES:
            _add_function_symbol(child, symbols)
    return symbols


def _decode(node: Node) -> str:
    text = node.text
    return text.decode("utf-8") if text is not None else ""


def _is_exported(name: str) -> bool:
    return bool(name) and name[0:1].isupper()


def _add_type_spec_symbols(decl_node: Node, symbols: list[Symbol]) -> None:
    for spec in decl_node.children:
        if spec.type != "type_spec":
            continue
        name_node = spec.child_by_field_name("name")
        type_node = spec.child_by_field_name("type")
        if name_node is None or type_node is None:
            continue
        kind = _TYPE_SPEC_KINDS.get(type_node.type)
        if kind is None:
            continue
        name = _decode(name_node)
        symbols.append(
            Symbol(name=name, kind=kind, line=spec.start_point.row + 1, is_public=_is_exported(name))
        )


def _add_function_symbol(node: Node, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _decode(name_node)
    kind: SymbolKind = "method" if node.type == "method_declaration" else "function"
    symbols.append(
        Symbol(
            name=name,
            kind=kind,
            line=node.start_point.row + 1,
            is_public=_is_exported(name),
            is_entry_point=name in ENTRY_POINT_NAMES,
        )
    )


def extract_relationships_go(source: str) -> list[Relationship]:
    """Return every deterministic import/calls relationship Go's grammar
    makes explicit (QB-067) — file-local and unresolved, see
    `relationship_model.py`'s module docstring.

    - **imports**: every `import_spec` (single or grouped `import (...)`
      form). `target` is the raw import path exactly as written (e.g.
      `"fmt"`, `"pkg/sub"`). `qualifier` is the local package identifier
      the import binds: an explicit alias (`f "fmt"`) or blank/dot import
      (`_`/`.`, recorded literally — neither ever resolves to a real
      symbol, so they are harmless, honest dead ends downstream) when
      present; otherwise the import path's own last segment (Go's own,
      near-universal package-naming convention — idiomatic Go always
      names a package to match its directory's final path element, the
      same convention `go vet`/`gopls` themselves rely on — not a Quor
      guess, and documented here as a bounded, real-language-convention
      assumption, not a heuristic invented for this feature).
    - **inherits / implements_interface / implements_trait / overrides**:
      never emitted. Go has no class inheritance at all, and interface
      satisfaction is *structural* (a type implements an interface simply
      by having the right method set, with no `implements`-style
      declaration anywhere in the source) — there is no syntactic marker
      to extract this from without a full structural type check, which is
      out of scope for a single-file AST extractor. A real, documented
      language limitation, not an oversight.
    - **calls**: only from within a function/method already reported as a
      `Symbol` by `extract_symbols_go()` (top-level functions and
      methods) — `source` is that `Symbol`'s own name. A bare call
      (`helper()`) has `qualifier=None`; a selector call
      (`pkg.Func()`/`w.Render()`) has `qualifier=<operand name>` — Go has
      no `self`/`this`, so a method call through a receiver variable
      (`w.Render()`) is recorded the same shape as a package-qualified
      call (`pkg.Func()`); the orchestrator only resolves it if
      `qualifier` happens to match a real import binding, so a receiver
      call harmlessly stays unresolved unless its variable name
      coincides with an unrelated import alias (documented, low-risk
      ambiguity — resolution additionally requires the target name to
      exist in that specific resolved file, making an accidental false
      match rare).

    Returns an empty list (with the same actionable warning
    `analyze_go()` emits) if the optional dependency is missing.
    Otherwise may raise on a genuine, unrecoverable parser failure — same
    fail-open contract as `analyze_go()`."""
    try:
        import tree_sitter
        import tree_sitter_go
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-go is not installed; "
            "install quor[go] to enable Go relationship extraction "
            "(falling back to no relationships for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_go.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    relationships: list[Relationship] = []
    for child in tree.root_node.children:
        if child.type == "import_declaration":
            _add_import_declaration_relationships(child, relationships)
        elif child.type in _FUNCTION_LIKE_TYPES:
            _add_function_relationships(child, relationships)
    return relationships


def _string_literal_text(node: Node) -> str:
    for child in node.children:
        if child.type in ("interpreted_string_literal_content", "raw_string_literal_content"):
            return _decode(child)
    return ""


def _add_import_declaration_relationships(node: Node, relationships: list[Relationship]) -> None:
    for child in node.children:
        if child.type == "import_spec":
            _add_import_spec_relationship(child, relationships)
        elif child.type == "import_spec_list":
            for spec in child.children:
                if spec.type == "import_spec":
                    _add_import_spec_relationship(spec, relationships)


def _add_import_spec_relationship(spec: Node, relationships: list[Relationship]) -> None:
    path_node = spec.child_by_field_name("path")
    if path_node is None:
        return
    target = _string_literal_text(path_node)
    name_node = spec.child_by_field_name("name")
    qualifier = _decode(name_node) if name_node is not None else (target.rsplit("/", 1)[-1] or None)
    relationships.append(
        Relationship(kind="import", source="", target=target, line=spec.start_point.row + 1, qualifier=qualifier)
    )


def _add_function_relationships(node: Node, relationships: list[Relationship]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    _collect_calls(node, _decode(name_node), relationships)


def _collect_calls(node: Node, source_name: str, relationships: list[Relationship]) -> None:
    for descendant in iter_descendants(node):
        if descendant.type != "call_expression":
            continue
        func = descendant.child_by_field_name("function")
        if func is None:
            continue
        line = descendant.start_point.row + 1
        if func.type == "identifier":
            relationships.append(
                Relationship(kind="calls", source=source_name, target=_decode(func), line=line)
            )
        elif func.type == "selector_expression":
            operand = func.child_by_field_name("operand")
            field = func.child_by_field_name("field")
            if operand is not None and operand.type == "identifier" and field is not None:
                relationships.append(
                    Relationship(
                        kind="calls",
                        source=source_name,
                        target=_decode(field),
                        line=line,
                        qualifier=_decode(operand),
                    )
                )
