"""C# analyzer for the AST summarization framework (QB-046).

Compresses method/constructor/lambda **bodies** to nothing, preserving
everything that describes a file's public surface (using directives,
namespace/class/interface/struct signatures — including base/interface
lists via `:` — field declarations, method/constructor signatures, XML doc
comments) — the same compression philosophy `go.py`/`java.py` already
implement, mapped onto C#'s AST node shapes.

Uses `tree-sitter` + `tree-sitter-c-sharp` (optional dependency,
`quor[csharp]` — its own dedicated extra, mirroring `go.py`/`java.py`/
`rust.py`'s identical "own extra, not folded into `quor[javascript]`"
choice). `tree_sitter`/`tree_sitter_c_sharp` are imported **lazily, inside
`analyze_csharp()`**, not at module top level — mirrors `java.py`'s
identical lazy-import discipline, which is what lets `registry.py` register
`"csharp"` **unconditionally**.

Public API: `analyze_csharp(source: str) -> set[int]` — returns the
1-indexed line numbers eligible for compression. Same return-type contract
as `analyze_python()`/`analyze_go()`/`analyze_java()`.

Fail-open contract — identical shape to `java.py` (see its module docstring
for the full reasoning): a missing `tree-sitter`/`tree-sitter-c-sharp`
dependency is caught here and warns; a genuine parse failure on real C#
source is not caught here and propagates to `Pipeline.execute()`'s
per-stage fail-open (ADR-018).

Structurally closer to `java.py` than to `go.py` (methods/constructors/
lambdas all live inside a class/interface/struct body, not flat at top
level), with one addition Java doesn't need: a block-scoped `namespace X {
... }` wraps its contents in its own `declaration_list` body (empirically
verified against the installed `tree-sitter-c-sharp` grammar while
implementing this module) — so this module recurses through zero or more
levels of `namespace_declaration` before reaching a type declaration, one
level further than `java.py`'s single top-level-to-class-body hop. A
file-scoped `namespace X;` (C# 10+) does NOT wrap anything — empirically it
leaves top-level declarations as direct `compilation_unit` siblings, so it
needs no unwrapping at all and isn't special-cased here.

Reuses `_treesitter_utils.py`'s `collect_error_ranges()`/`add_candidate()`
unmodified — unlike `java.py` (whose method/constructor bodies are two
*different* node types, `block`/`constructor_body`), this grammar names a
method body and a constructor body identically (`block`, empirically
verified), so — like `go.py` — this module needs only one `block_type`
constant, not `java.py`'s per-member dict.
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

# C#'s own block node type name (method/constructor/statement-lambda bodies
# all share this one type — see module docstring) — passed to
# _treesitter_utils.add_candidate()/statement_block_interior_lines().
_BLOCK_TYPE = "block"

# Block-scoped namespace node type (per tree-sitter-c-sharp's grammar,
# empirically verified against the installed grammar version while
# implementing this module) whose own `body` field is a `declaration_list`
# worth recursing through. `file_scoped_namespace_declaration` (`namespace
# X;`, C# 10+) is deliberately NOT included — empirically it wraps nothing;
# the declarations it "contains" remain direct top-level siblings, already
# reached without any unwrapping.
_NAMESPACE_TYPE = "namespace_declaration"

# Type-declaration node types whose own `body` field is a `declaration_list`
# eligible for one level of member traversal — class, interface, and struct
# all share this exact same body node type in this grammar (unlike Java's
# distinct class_body/interface_body). `record_declaration`/
# `enum_declaration` are deliberately NOT included — out of scope for this
# pass, mirroring `java.py`'s identical "narrow, documented limitation" for
# its own enum_declaration/record_declaration exclusion.
_TYPE_DECLARATION_TYPES = frozenset({"class_declaration", "interface_declaration", "struct_declaration"})
_TYPE_BODY = "declaration_list"

# Class/interface/struct member node types that have a `body` field naming a
# brace-delimited block eligible for compression. Both share `block` as
# their body's node type in this grammar (see module docstring) — one
# constant, not `java.py`'s per-member `block_type` dict.
_METHOD_LIKE_TYPES = frozenset({"method_declaration", "constructor_declaration"})


def analyze_csharp(source: str) -> set[int]:
    """Return the 1-indexed line numbers of C# method/constructor/lambda
    BODY lines eligible for compression.

    Returns an empty set (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-c-sharp` dependency is not installed.
    Otherwise may raise on a genuine, unrecoverable parser failure — not
    caught here, see module docstring "Fail-open".
    """
    try:
        import tree_sitter
        import tree_sitter_c_sharp
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-c-sharp is not installed; "
            "install quor[csharp] to enable C# AST summarization "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return set()

    language = tree_sitter.Language(tree_sitter_c_sharp.language())
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
    """Walk `node`'s children looking for top-level (or namespace-nested)
    `class_declaration`/`interface_declaration`/`struct_declaration` nodes
    and recurse one level into each one's own body.

    A block-scoped `namespace_declaration` is unwrapped by recursing this
    same function into its own `declaration_list` body — handles arbitrarily
    nested `namespace A { namespace B { ... } }` blocks for free, since it's
    the exact same child-scanning logic applied one level deeper each time.
    Mirrors `java.py`'s `_visit_top_level()` shape otherwise, adapted for
    C#'s optional namespace-wrapping layer.
    """
    for child in node.children:
        if child.type == _NAMESPACE_TYPE:
            body = child.child_by_field_name("body")
            if body is not None and body.type == _TYPE_BODY:
                _visit_top_level(body, error_ranges, lines)
        elif child.type in _TYPE_DECLARATION_TYPES:
            _visit_type_body(child, error_ranges, lines)


def _visit_type_body(type_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]) -> None:
    """Recurse exactly one level into `type_node`'s own body (a
    `declaration_list`), compressing each method/constructor body
    independently and each lambda-valued field independently. Does not
    recurse into a member's own body any further, and does not visit a
    member class/interface/struct nested inside this body at all — that
    member's own methods are therefore not found or compressed, a
    documented limitation mirroring `java.py`'s identical scope boundary for
    nested Java classes."""
    body = type_node.child_by_field_name("body")
    if body is None or body.type != _TYPE_BODY:
        return
    for member in body.children:
        if member.type in _METHOD_LIKE_TYPES:
            add_candidate(member, error_ranges, lines, block_type=_BLOCK_TYPE)
        elif member.type == "field_declaration":
            _visit_field_declaration(member, error_ranges, lines)


def _visit_field_declaration(
    decl_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]
) -> None:
    """For each `variable_declarator` in a `field_declaration` (C# allows
    `private int a, b;` — more than one declarator per declaration),
    compress the assigned value's body if that value is a
    `lambda_expression` with a block body.

    Unlike `go.py`'s `var_spec`/`java.py`'s `variable_declarator` (both of
    which name their assigned-value child via a `value` field),
    `variable_declarator` in this grammar has no named field for its
    initializer (empirically verified against the installed grammar) — so,
    uniquely among the four analyzers, this scans the declarator's children
    directly for a `lambda_expression` rather than using
    `child_by_field_name("value")`.
    """
    for child in decl_node.children:
        if child.type != "variable_declaration":
            continue
        for declarator in child.children:
            if declarator.type != "variable_declarator":
                continue
            for value in declarator.children:
                if value.type == "lambda_expression":
                    add_candidate(value, error_ranges, lines, block_type=_BLOCK_TYPE)


# Type-declaration node types (QB-066 symbol extraction) mapped to the
# Symbol kind each represents — extends `_TYPE_DECLARATION_TYPES` above
# (class/interface/struct only, for compression purposes) with
# `enum_declaration`, which the compression side deliberately excludes (see
# module docstring) but which symbol extraction must report since "Enums"
# is one of this feature's required categories. Enum members are not
# themselves visited (no method/field body to compress or report).
_TYPE_DECLARATION_KINDS: dict[str, SymbolKind] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "struct_declaration": "struct",
    "enum_declaration": "enum",
}


def extract_symbols_csharp(source: str) -> list[Symbol]:
    """Return every (optionally namespace-nested) class/interface/struct/
    enum declaration and each class/interface/struct's direct methods/
    constructors (QB-066), in source order. A member nested inside another
    type's body is not itself visited — the same one-level scope boundary
    `_visit_type_body()` already applies for compression.

    `is_public` reflects an explicit `public` modifier — C#'s own default
    for an unmarked top-level type is internal and for an unmarked member
    is private, neither public, so an unmarked declaration is
    `is_public=False`.

    Returns an empty list (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-c-sharp` dependency is not installed.
    Otherwise may raise on a genuine, unrecoverable parser failure — not
    caught here, same fail-open contract as `analyze_csharp()`."""
    try:
        import tree_sitter
        import tree_sitter_c_sharp
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-c-sharp is not installed; "
            "install quor[csharp] to enable C# symbol extraction "
            "(falling back to no symbols for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_c_sharp.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    symbols: list[Symbol] = []
    _visit_top_level_symbols(tree.root_node, symbols)
    return symbols


def _decode(node: Node) -> str:
    text = node.text
    return text.decode("utf-8") if text is not None else ""


def _has_public_modifier(node: Node) -> bool:
    return any(child.type == "modifier" and _decode(child) == "public" for child in node.children)


def _visit_top_level_symbols(node: Node, symbols: list[Symbol]) -> None:
    for child in node.children:
        if child.type == _NAMESPACE_TYPE:
            body = child.child_by_field_name("body")
            if body is not None and body.type == _TYPE_BODY:
                _visit_top_level_symbols(body, symbols)
        else:
            kind = _TYPE_DECLARATION_KINDS.get(child.type)
            if kind is not None:
                _add_type_symbol(child, symbols, kind=kind)


def _add_type_symbol(node: Node, symbols: list[Symbol], *, kind: SymbolKind) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    symbols.append(
        Symbol(
            name=_decode(name_node),
            kind=kind,
            line=node.start_point.row + 1,
            is_public=_has_public_modifier(node),
        )
    )
    if kind == "enum":
        return
    body = node.child_by_field_name("body")
    if body is None:
        return
    for member in body.children:
        if member.type in _METHOD_LIKE_TYPES:
            _add_method_symbol(member, symbols)


def _add_method_symbol(node: Node, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _decode(name_node)
    symbols.append(
        Symbol(
            name=name,
            kind="method",
            line=node.start_point.row + 1,
            is_public=_has_public_modifier(node),
            is_entry_point=name in ENTRY_POINT_NAMES,
        )
    )


def _flatten_qualified_name(node: Node) -> str | None:
    if node.type == "identifier":
        return _decode(node)
    if node.type == "qualified_name":
        qualifier = node.child_by_field_name("qualifier")
        name = node.child_by_field_name("name")
        if qualifier is not None and name is not None:
            base = _flatten_qualified_name(qualifier)
            return f"{base}.{_decode(name)}" if base is not None else None
    return None


def extract_relationships_csharp(source: str) -> list[Relationship]:
    """Return every deterministic import/inherits/overrides/calls
    relationship C#'s grammar makes explicit (QB-067) — file-local and
    unresolved, see `relationship_model.py`'s module docstring.

    - **imports**: every `using` directive's dotted namespace. `target`
      is the full dotted name; `qualifier` is always `None` — unlike
      every other language here, a C# `using` opens a *namespace*, not a
      specific type or member binding, so there is no single local name
      it introduces to resolve a bare identifier against (a real,
      documented language-semantics limitation: cross-file `inherits`/
      `calls` resolution for C# is therefore same-file-only, since the
      orchestrator's binding table has nothing to key a `using` on — see
      `graph.py`'s own resolution notes). `using static`/alias
      (`using X = Y;`) directives are handled the same shape-detection
      way as a plain `using` (both are `using_directive` nodes); an
      alias's own local name is not treated as a binding for the same
      "no addressable name" reason.
    - **inherits**: every entry in a class's colon-delimited base list
      (`class Foo : Base, IBar, IBaz`) is reported as `inherits` — C#'s
      grammar (and this extractor) cannot syntactically distinguish a
      base *class* from an implemented *interface* in that list without
      resolving each name's own declaration (a class can appear with no
      base class at all, `class Foo : IBar`, which is syntactically
      identical to `class Foo : Base`) — a real, documented limitation,
      not a naming-convention guess (`I`-prefix is a convention, not a
      language rule, and QB-067 rules out heuristics). `implements_interface`
      is therefore never emitted for C# (see `java.py`/`typescript.py` for
      languages whose grammar keeps these syntactically separate).
    - **implements_trait**: never emitted — C# has no trait construct.
    - **overrides**: a method carrying an explicit `override` modifier —
      `qualifier` is the enclosing class's own raw base-list (first
      entry only, the conventional base-class position) name; a method
      with no `override` modifier is not reported.
    - **calls**: only from within a method/constructor already reported
      as a `Symbol` by `extract_symbols_csharp()` — `source` is that
      `Symbol`'s own name. A bare call (`Helper()`), an explicit
      `this.Other()`/`base.Method()` call, and a qualified call
      (`Utils.StaticCall()`) are each recorded — a deeper chain is
      skipped.

    Returns an empty list (with the same actionable warning
    `analyze_csharp()` emits) if the optional dependency is missing.
    Otherwise may raise on a genuine, unrecoverable parser failure — same
    fail-open contract as `analyze_csharp()`."""
    try:
        import tree_sitter
        import tree_sitter_c_sharp
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-c-sharp is not installed; "
            "install quor[csharp] to enable C# relationship extraction "
            "(falling back to no relationships for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_c_sharp.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    relationships: list[Relationship] = []
    for child in tree.root_node.children:
        if child.type == "using_directive":
            _add_using_relationship(child, relationships)
    _visit_top_level_relationships(tree.root_node, relationships)
    return relationships


def _add_using_relationship(node: Node, relationships: list[Relationship]) -> None:
    name_node = next((c for c in node.children if c.type in ("identifier", "qualified_name")), None)
    if name_node is None:
        return
    target = _flatten_qualified_name(name_node)
    if target is not None:
        relationships.append(Relationship(kind="import", source="", target=target, line=node.start_point.row + 1))


def _visit_top_level_relationships(node: Node, relationships: list[Relationship]) -> None:
    for child in node.children:
        if child.type == _NAMESPACE_TYPE:
            body = child.child_by_field_name("body")
            if body is not None and body.type == _TYPE_BODY:
                _visit_top_level_relationships(body, relationships)
        elif child.type in _TYPE_DECLARATION_TYPES:
            _add_type_relationships(child, relationships)


def _add_type_relationships(node: Node, relationships: list[Relationship]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    type_name = _decode(name_node)

    superclass_name: str | None = None
    base_list = next((c for c in node.children if c.type == "base_list"), None)
    if base_list is not None:
        bases = [c for c in base_list.children if c.is_named]
        for index, base_node in enumerate(bases):
            base_name = _flatten_qualified_name(base_node) or (
                _decode(base_node) if base_node.type == "identifier" else None
            )
            if base_name is None:
                continue
            if index == 0:
                superclass_name = base_name
            relationships.append(
                Relationship(
                    kind="inherits", source=type_name, target=base_name, line=base_node.start_point.row + 1
                )
            )

    body = node.child_by_field_name("body")
    if body is None:
        return
    for member in body.children:
        if member.type in _METHOD_LIKE_TYPES:
            _add_method_relationships(member, superclass_name, relationships)


def _has_override_modifier(node: Node) -> bool:
    return any(child.type == "modifier" and _decode(child) == "override" for child in node.children)


def _add_method_relationships(
    node: Node, superclass_name: str | None, relationships: list[Relationship]
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    method_name = _decode(name_node)
    _collect_calls(node, method_name, relationships)
    if superclass_name is not None and _has_override_modifier(node):
        relationships.append(
            Relationship(
                kind="overrides",
                source=method_name,
                target=method_name,
                line=node.start_point.row + 1,
                qualifier=superclass_name,
            )
        )


def _collect_calls(node: Node, source_name: str, relationships: list[Relationship]) -> None:
    for descendant in iter_descendants(node):
        if descendant.type != "invocation_expression":
            continue
        func = descendant.child_by_field_name("function")
        if func is None:
            continue
        line = descendant.start_point.row + 1
        if func.type == "identifier":
            relationships.append(
                Relationship(kind="calls", source=source_name, target=_decode(func), line=line)
            )
        elif func.type == "member_access_expression":
            name_node = func.child_by_field_name("name")
            expr_node = func.child_by_field_name("expression")
            if name_node is None:
                continue
            if expr_node is not None and expr_node.type in ("this", "base"):
                relationships.append(
                    Relationship(
                        kind="calls",
                        source=source_name,
                        target=_decode(name_node),
                        line=line,
                        qualifier=expr_node.type,
                    )
                )
            elif expr_node is not None and expr_node.type == "identifier":
                relationships.append(
                    Relationship(
                        kind="calls",
                        source=source_name,
                        target=_decode(name_node),
                        line=line,
                        qualifier=_decode(expr_node),
                    )
                )
