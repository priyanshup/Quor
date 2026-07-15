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

from quor.pipeline.ast_summarize._treesitter_utils import add_candidate, collect_error_ranges

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
