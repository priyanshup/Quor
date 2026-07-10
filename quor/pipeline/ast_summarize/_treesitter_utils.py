"""Shared, language-agnostic tree-sitter helpers (QB-005D).

Extracted from `quor/pipeline/ast_summarize/javascript.py` (QB-005C) when
`typescript.py` (QB-005D) needed the exact same ERROR-node-overlap
exclusion rule and body-interior-line computation — the task's own
instruction ("reuse the same ERROR-node exclusion rule implemented for
JavaScript") meant actual code reuse, not a second, drifting
reimplementation. Every function here operates purely on the generic
tree-sitter `Node` API (`.type`, `.children`, `.start_point`, `.end_point`,
`.child_by_field_name()`, `.is_missing`) with zero JavaScript- or
TypeScript-specific knowledge baked in — the same reasoning
`quor/pipeline/stages/_utils.py` already applies to helpers shared across
multiple compression stages (`_compile`, `matches_any`, etc.), just one
package level down, for helpers shared across multiple *language
analyzers* instead of multiple *stages*.

This module is NOT itself an analyzer — it has no `analyze_*()` public
entry point, and `quor/pipeline/ast_summarize/registry.py` never imports it
directly. `javascript.py` and `typescript.py` are the only callers.

Extracting these functions is a pure relocation of already-correct,
already-tested logic, not a rewrite — `javascript.py`'s own observable
behavior was re-verified byte-for-byte unchanged after this refactor (see
backlog.md's QB-005D entry for the before/after proof), the same discipline
QB-005B applied when relocating `python_ast_summarize.py`'s internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


def statement_block_interior_lines(node: Node) -> set[int]:
    """Return the 1-indexed line numbers strictly between a function-like
    node's `{` and `}` lines — i.e. its actual body content, excluding both
    brace lines. The opening `{` is, in standard JS/TS style, on the same
    physical line as the signature (`function add(x, y) {`), so — unlike
    Python, which has no brace to preserve — the brace lines themselves
    must be explicitly excluded from compression, not just the signature
    text before them, or the signature itself would be destroyed.

    Empty for: a body that isn't a `statement_block` at all (a
    single-expression arrow function, e.g. `(a) => a + 1`, or a
    signature-only declaration with no body at all — an overload signature
    or an abstract method — mirrors Python's same-line-body rule,
    generalized: there is no brace-delimited block to compress in the
    first place), or a `statement_block` whose open/close braces are on
    the same or adjacent lines (a same-line body `function f() { return
    1; }`, or a genuinely empty body `function f() {\\n}` — nothing
    meaningful to compress either way).
    """
    body = node.child_by_field_name("body")
    if body is None or body.type != "statement_block":
        return set()

    start_row = body.start_point.row  # row of "{"
    end_row = body.end_point.row  # row of "}"
    if end_row <= start_row + 1:
        return set()

    # 1-indexed lines strictly between the brace lines.
    return set(range(start_row + 2, end_row + 1))


def collect_error_ranges(node: Node) -> list[tuple[int, int]]:
    """Walk the full tree once, returning (start_row, end_row) 0-indexed,
    inclusive ranges for every `ERROR` node and every synthetic `MISSING`
    node tree-sitter inserted during error recovery.

    Should be called at most once per analyzer invocation, and only when
    the tree's `root.has_error` is already `True` — a clean file pays
    nothing for this (both `javascript.py` and `typescript.py` gate the
    call this way).
    """
    ranges: list[tuple[int, int]] = []

    def visit(n: Node) -> None:
        if n.type == "ERROR" or n.is_missing:
            ranges.append((n.start_point.row, n.end_point.row))
        for child in n.children:
            visit(child)

    visit(node)
    return ranges


def has_error_overlap(node: Node, error_ranges: list[tuple[int, int]]) -> bool:
    """QB-005A Section 4.1's mandatory rule: a function/method whose own
    signature-to-closing-brace span overlaps *any* ERROR/MISSING node
    anywhere in the tree must never be summarized — not because that
    specific function is necessarily unsafe, but because a nearby
    malformed construct can shift what tree-sitter believes that
    function's own boundaries are, and Quor's "meaning preservation is
    non-negotiable" principle means the conservative default wins on any
    doubt. Uses `node`'s own full span (signature through closing brace),
    not just its body's span, per the design's explicit wording."""
    node_start = node.start_point.row
    node_end = node.end_point.row
    return any(err_start <= node_end and err_end >= node_start for err_start, err_end in error_ranges)


def add_candidate(node: Node, error_ranges: list[tuple[int, int]], lines: set[int]) -> None:
    """Compute `node`'s body-interior compress range and, unless it
    overlaps an ERROR/MISSING node anywhere in the tree (QB-005A Section
    4.1 — mandatory, not optional), add it to `lines`."""
    candidate = statement_block_interior_lines(node)
    if not candidate:
        return
    if has_error_overlap(node, error_ranges):
        return
    lines.update(candidate)
