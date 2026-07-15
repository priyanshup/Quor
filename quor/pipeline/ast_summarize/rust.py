"""Rust analyzer for the AST summarization framework (QB-046).

Compresses function/method **bodies** to nothing, preserving everything
that describes a file's public surface (`use` declarations, `const`/
`static` items, struct/enum/trait signatures, `impl`/`trait` headers,
function/method signatures, doc comments) — the same compression
philosophy `python.py`/`javascript.py`/`go.py`/`java.py` already
implement, mapped onto Rust's AST node shapes.

Uses `tree-sitter` + `tree-sitter-rust` (optional dependency, `quor[rust]`
— its own extra, mirroring `go.py`/`java.py`'s "own dedicated extra rather
than folded into `quor[javascript]`" choice, for the same reason: a
Rust-only user shouldn't pull in grammars for languages they have no use
for). `tree_sitter`/`tree_sitter_rust` are imported **lazily, inside
`analyze_rust()`**, not at module top level — mirrors `go.py`/`java.py`'s
identical lazy-import discipline, which is what lets `registry.py`
register `"rust"` **unconditionally**.

Public API: `analyze_rust(source: str) -> set[int]` — returns the
1-indexed line numbers eligible for compression. Same return-type
contract as `analyze_python()`/`analyze_javascript()`/`analyze_go()`/
`analyze_java()`.

Fail-open contract — identical shape to `go.py`/`java.py` (see their
module docstrings for the full reasoning): a missing
`tree-sitter`/`tree-sitter-rust` dependency is caught here and warns; a
genuine parse failure on real Rust source is not caught here and
propagates to `Pipeline.execute()`'s per-stage fail-open (ADR-018).

Rust's grammar names every function-like construct (free function,
inherent method, trait method with a default body) the same node type,
`function_item`, with a `block`-typed `body` field — unlike Java, there is
no separate constructor-body node type to special-case. A method lives
inside an `impl`/`trait` block's own `declaration_list` body, one level
deeper than a top-level `function_item` — structurally closer to
`java.py`'s class-nested layout than to `go.py`'s flat top-level walk, so
this module recurses exactly one level into a top-level `impl_item`/
`trait_item`'s own body, exactly as far as `java.py`'s
`_visit_type_body()` recurses into a Java class — no further (a module
(`mod`) nested inside a file, or an `impl`/`trait` nested inside another
item, is not itself visited; a documented limitation mirroring
`java.py`'s identical "member class/interface/enum nested inside this
body" scope boundary).

A trait method with no default implementation (`fn area(&self) -> f64;`)
is its own distinct node type, `function_signature_item` — not a
`function_item` with an absent body — so it is naturally never selected
for compression; there is no body to compress in the first place.

Rust has no top-level `let`/closure-assignment analog to Go's package-level
`var f = func() {...}` — a top-level `let` is not legal Rust outside a
function body (`const`/`static` initializers cannot hold a closure in
stable Rust either) — so, unlike `go.py`'s `_visit_var_declaration()`,
this module has no closure-in-declaration case to handle at all.

Reuses `_treesitter_utils.py`'s `collect_error_ranges()`/`add_candidate()`
unmodified in spirit — every function-like node's body is uniformly
`"block"` (Rust's own grammar name for the node, same as Go's — not JS/
TS's `"statement_block"`, `add_candidate()`'s default), so every call site
here passes `block_type=_BLOCK_TYPE` explicitly, mirroring `go.py`'s
identical need to override the default.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from quor.pipeline.ast_summarize._treesitter_utils import add_candidate, collect_error_ranges

if TYPE_CHECKING:
    from tree_sitter import Node

# Top-level node types (per tree-sitter-rust's grammar, empirically
# verified against the installed grammar version while implementing this
# module) that own a `body` field naming a `declaration_list` eligible for
# one level of member traversal — an inherent `impl` block, a trait `impl`
# block (`impl Trait for Type { ... }` — same node type, same field shape),
# and a `trait` definition.
_CONTAINER_TYPES = frozenset({"impl_item", "trait_item"})

# The one brace-delimited body node type tree-sitter-rust's grammar uses
# for every container (impl/trait) body.
_CONTAINER_BODY_TYPE = "declaration_list"

# Rust's own block node type name (vs. JS/TS's "statement_block") — passed
# to _treesitter_utils.add_candidate()/statement_block_interior_lines(),
# same as go.py's identical override.
_BLOCK_TYPE = "block"


def analyze_rust(source: str) -> set[int]:
    """Return the 1-indexed line numbers of Rust function/method BODY lines
    eligible for compression.

    Returns an empty set (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-rust` dependency is not installed. Otherwise
    may raise on a genuine, unrecoverable parser failure — not caught here,
    see module docstring "Fail-open".
    """
    try:
        import tree_sitter
        import tree_sitter_rust
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-rust is not installed; "
            "install quor[rust] to enable Rust AST summarization "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return set()

    language = tree_sitter.Language(tree_sitter_rust.language())
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
    """Walk `node`'s children looking for top-level `function_item`
    declarations and `impl`/`trait` blocks, recursing one level into each
    container's own body. Mirrors `go.py`'s `_visit_top_level()` shape for
    the flat top-level case, and `java.py`'s `_visit_type_body()` recursion
    depth for the container case.

    Once a function-like node is selected for body compression, this
    function does not recurse into it any further — a closure defined
    inside a function's body is implementation detail of the outer one,
    mirroring `python.py`/`javascript.py`/`go.py`/`java.py`'s identical
    "no further recursion" rule.
    """
    for child in node.children:
        if child.type == "function_item":
            add_candidate(child, error_ranges, lines, block_type=_BLOCK_TYPE)
        elif child.type in _CONTAINER_TYPES:
            _visit_container_body(child, error_ranges, lines)


def _visit_container_body(
    container_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]
) -> None:
    """Recurse exactly one level into `container_node`'s own body (a
    `declaration_list`), compressing each member `function_item`'s body
    independently. Does not recurse into a member's own body any further,
    and does not visit an `impl`/`trait`/`mod` nested inside this body at
    all — a documented limitation mirroring `java.py`'s identical scope
    boundary for a member class/interface/enum nested inside a Java class
    body."""
    body = container_node.child_by_field_name("body")
    if body is None or body.type != _CONTAINER_BODY_TYPE:
        return
    for member in body.children:
        if member.type == "function_item":
            add_candidate(member, error_ranges, lines, block_type=_BLOCK_TYPE)
