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

from quor.pipeline.ast_summarize.import_model import ImportedName, ImportStatement

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node


def statement_block_interior_lines(node: Node, block_type: str = "statement_block") -> set[int]:
    """Return the 1-indexed line numbers strictly between a function-like
    node's `{` and `}` lines — i.e. its actual body content, excluding both
    brace lines. The opening `{` is, in standard JS/TS style, on the same
    physical line as the signature (`function add(x, y) {`), so — unlike
    Python, which has no brace to preserve — the brace lines themselves
    must be explicitly excluded from compression, not just the signature
    text before them, or the signature itself would be destroyed.

    `block_type` names the grammar's own node type for a brace-delimited
    block (`"statement_block"` for JS/TS, `"block"` for Go — QB-046) —
    defaults to the JS/TS value so existing callers are unaffected.

    Empty for: a body that isn't a block of the expected type at all (a
    single-expression arrow function, e.g. `(a) => a + 1`, or a
    signature-only declaration with no body at all — an overload signature
    or an abstract method — mirrors Python's same-line-body rule,
    generalized: there is no brace-delimited block to compress in the
    first place), or a block whose open/close braces are on
    the same or adjacent lines (a same-line body `function f() { return
    1; }`, or a genuinely empty body `function f() {\\n}` — nothing
    meaningful to compress either way).
    """
    body = node.child_by_field_name("body")
    if body is None or body.type != block_type:
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


def iter_descendants(node: Node) -> Iterator[Node]:
    """Yield every descendant of `node` (not including `node` itself),
    depth-first — the tree-sitter equivalent of stdlib `ast.walk()`'s full
    subtree traversal (QB-067). Used by every language's
    `extract_relationships_*()` to find every call expression anywhere
    inside a function/method's body, including inside a nested closure —
    mirroring `python.py`'s own "a call inside a nested def is attributed to
    its nearest enclosing top-level/class-method Symbol" rule, generalized
    to the tree-sitter grammars.
    """
    for child in node.children:
        yield child
        yield from iter_descendants(child)


def add_candidate(
    node: Node,
    error_ranges: list[tuple[int, int]],
    lines: set[int],
    block_type: str = "statement_block",
) -> None:
    """Compute `node`'s body-interior compress range and, unless it
    overlaps an ERROR/MISSING node anywhere in the tree (QB-005A Section
    4.1 — mandatory, not optional), add it to `lines`.

    `block_type` is passed straight through to
    `statement_block_interior_lines()` — see its own docstring."""
    candidate = statement_block_interior_lines(node, block_type)
    if not candidate:
        return
    if has_error_overlap(node, error_ranges):
        return
    lines.update(candidate)


def _decode(node: Node) -> str:
    text = node.text
    return text.decode("utf-8") if text is not None else ""


def _es_string_text(node: Node) -> str:
    """Decode a `string` node's inner text (between its quote tokens) —
    empty for an empty string literal, whose `string` node has no
    `string_fragment` child at all. Mirrors javascript.py's/typescript.py's
    own (independently duplicated) `_string_source_text()`; this one is
    private to `extract_es_import_statements()` below, not a further
    consolidation of those two — see this module's own docstring on why
    `extract_relationships_*()`'s pre-existing duplication is left alone."""
    for child in node.children:
        if child.type == "string_fragment":
            return _decode(child)
    return ""


def extract_es_import_statements(root: Node) -> list[ImportStatement]:
    """Return every top-level ESM `import_statement` (QB-096), in source
    order — not a CommonJS `require()` call, and not an `export ... from`
    re-export, mirroring `extract_relationships_javascript()`'s own
    "`require()` is an ordinary call, not a static import" distinction.

    Shared by `javascript.py` and `typescript.py`'s own
    `collapse_imports_*()` because tree-sitter-javascript and
    tree-sitter-typescript expose byte-identical node/field shapes for this
    one grammar construct (`import_statement`/`import_clause`/
    `namespace_import`/`named_imports`/`import_specifier` — empirically
    confirmed against both installed grammars, matching how
    `extract_relationships_typescript()`'s own `_add_import_relationships()`
    already duplicates `extract_relationships_javascript()`'s identical
    logic verbatim; here the two languages' `collapse_imports_*()` call this
    one shared function instead of each keeping its own copy, since
    QB-096's own task explicitly asks to avoid a new, third copy of logic
    already proven identical twice)."""
    statements: list[ImportStatement] = []
    for child in root.children:
        if child.type == "import_statement":
            stmt = _import_statement_from_node(child)
            if stmt is not None:
                statements.append(stmt)
    return statements


def _import_statement_from_node(node: Node) -> ImportStatement | None:
    source_node = node.child_by_field_name("source")
    if source_node is None:
        return None
    module = _es_string_text(source_node)
    line = node.start_point.row + 1
    end_line = node.end_point.row + 1

    clause = next((c for c in node.children if c.type == "import_clause"), None)
    if clause is None:
        # Side-effect-only import: `import "./polyfill";` — binds no name.
        return ImportStatement(line=line, end_line=end_line, module=module)

    names: list[ImportedName] = []
    for child in clause.children:
        if child.type == "identifier":
            names.append(ImportedName(name=_decode(child)))
        elif child.type == "namespace_import":
            name_node = next((c for c in child.children if c.type == "identifier"), None)
            if name_node is not None:
                names.append(ImportedName(name="*", alias=_decode(name_node)))
        elif child.type == "named_imports":
            for specifier in child.children:
                if specifier.type != "import_specifier":
                    continue
                name_node = specifier.child_by_field_name("name")
                if name_node is None:
                    continue
                alias_node = specifier.child_by_field_name("alias")
                alias = _decode(alias_node) if alias_node is not None else None
                names.append(ImportedName(name=_decode(name_node), alias=alias))

    return ImportStatement(line=line, end_line=end_line, module=module, names=tuple(names))
