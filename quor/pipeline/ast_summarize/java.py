"""Java analyzer for the AST summarization framework (QB-046).

Compresses method/constructor/lambda **bodies** to nothing, preserving
everything that describes a file's public surface (package/import
declarations, class/interface signatures — including `extends`/
`implements`, field declarations, method/constructor signatures,
annotations, doc comments) — the same compression philosophy
`python.py`/`javascript.py`/`go.py` already implement, mapped onto Java's
AST node shapes.

Uses `tree-sitter` + `tree-sitter-java` (optional dependency, `quor[java]`
— its own extra, mirroring `go.py`'s "own dedicated extra rather than
folded into `quor[javascript]`" choice, for the same reason: a Java-only
user shouldn't pull in grammars for languages they have no use for).
`tree_sitter`/`tree_sitter_java` are imported **lazily, inside
`analyze_java()`**, not at module top level — mirrors `go.py`'s identical
lazy-import discipline, which is what lets `registry.py` register `"java"`
**unconditionally**.

Public API: `analyze_java(source: str) -> set[int]` — returns the
1-indexed line numbers eligible for compression. Same return-type contract
as `analyze_python()`/`analyze_javascript()`/`analyze_go()`.

Fail-open contract — identical shape to `go.py` (see its module docstring
for the full reasoning): a missing `tree-sitter`/`tree-sitter-java`
dependency is caught here and warns; a genuine parse failure on real Java
source is not caught here and propagates to `Pipeline.execute()`'s
per-stage fail-open (ADR-018).

Unlike Go (methods are top-level siblings, no classes at all), Java's
methods/constructors/lambdas all live **inside** a class or interface body
— structurally closer to `javascript.py`'s class handling than to
`go.py`'s flat top-level walk. This module therefore recurses one level
into a top-level `class_declaration`/`interface_declaration`'s own body,
exactly as far as `javascript.py`'s `_visit_class_body()` recurses into a
JS class — no further (a member class/interface/enum nested inside another
type's body is not itself visited; see `_visit_type_body()`'s own
docstring for the full, deliberate scope boundary this shares with the
JS/Go precedent).

Reuses `_treesitter_utils.py`'s `collect_error_ranges()`/`add_candidate()`
unmodified — `add_candidate()`/`statement_block_interior_lines()`'s
`block_type` parameter (added for `go.py`'s `"block"` vs. JS/TS's
`"statement_block"`) is used here too, but with **two** different values
depending on the member being compressed: `"block"` for a method/lambda
body, `"constructor_body"` for a constructor body — tree-sitter-java's
grammar genuinely names these two brace-delimited blocks differently
(empirically verified against the installed grammar version while
implementing this module), unlike Go/JS, where every function-like node's
body shares one node type.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from quor.pipeline.ast_summarize._treesitter_utils import add_candidate, collect_error_ranges

if TYPE_CHECKING:
    from tree_sitter import Node

# Top-level node types (per tree-sitter-java's grammar) that own a `body`
# field which may be a class-like body (`class_body`/`interface_body`)
# eligible for one level of member traversal. `enum_declaration` and
# `record_declaration` are deliberately NOT included — out of scope for
# this pass, same "narrow, documented limitation" discipline
# `javascript.py` applies to class *expressions* (see module docstring).
_CLASS_LIKE_BODY_TYPES: dict[str, str] = {
    "class_declaration": "class_body",
    "interface_declaration": "interface_body",
}

# Class/interface member node types that have a `body` field naming a
# brace-delimited block eligible for compression, mapped to that block's
# own node type name (see module docstring for why this differs between
# the two).
_METHOD_LIKE_BLOCK_TYPES: dict[str, str] = {
    "method_declaration": "block",
    "constructor_declaration": "constructor_body",
}


def analyze_java(source: str) -> set[int]:
    """Return the 1-indexed line numbers of Java method/constructor/
    lambda BODY lines eligible for compression.

    Returns an empty set (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-java` dependency is not installed.
    Otherwise may raise on a genuine, unrecoverable parser failure — not
    caught here, see module docstring "Fail-open".
    """
    try:
        import tree_sitter
        import tree_sitter_java
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-java is not installed; "
            "install quor[java] to enable Java AST summarization "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return set()

    language = tree_sitter.Language(tree_sitter_java.language())
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
    """Walk `node`'s children looking for top-level `class_declaration`/
    `interface_declaration` nodes and recurse one level into each one's own
    body. Mirrors `go.py`'s `_visit_top_level()` shape, adapted for Java's
    class-nested (rather than flat top-level) member layout."""
    for child in node.children:
        body_type = _CLASS_LIKE_BODY_TYPES.get(child.type)
        if body_type is not None:
            _visit_type_body(child, body_type, error_ranges, lines)


def _visit_type_body(
    type_node: Node, body_type: str, error_ranges: list[tuple[int, int]], lines: set[int]
) -> None:
    """Recurse exactly one level into `type_node`'s own body (a
    `class_body` or `interface_body`), compressing each method/constructor
    body independently and each lambda-valued field independently. Does
    not recurse into a member's own body any further (same "no further
    recursion" rule as `javascript.py`'s `_visit_class_body()`), and does
    not visit a member class/interface/enum nested inside this body at all
    — that member's own methods are therefore not found or compressed, a
    documented limitation mirroring `javascript.py`'s identical scope
    boundary for JS class expressions."""
    body = type_node.child_by_field_name("body")
    if body is None or body.type != body_type:
        return
    for member in body.children:
        block_type = _METHOD_LIKE_BLOCK_TYPES.get(member.type)
        if block_type is not None:
            add_candidate(member, error_ranges, lines, block_type=block_type)
        elif member.type == "field_declaration":
            _visit_field_declaration(member, error_ranges, lines)


def _visit_field_declaration(
    decl_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]
) -> None:
    """For each `variable_declarator` in a `field_declaration` (Java allows
    `private int a, b;` — more than one declarator per declaration),
    compress the assigned value's body if that value is a
    `lambda_expression` with a block body — mirrors `javascript.py`'s
    `_visit_variable_declaration()`, one container level deeper (a Java
    field lives inside a class/interface body, not at top level, since
    Java has no top-level variables at all)."""
    for declarator in decl_node.children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is not None and value.type == "lambda_expression":
            add_candidate(value, error_ranges, lines, block_type="block")
