"""JavaScript analyzer for the AST summarization framework (QB-005C).

Compresses function/method/arrow-function **bodies** to nothing, preserving
everything that describes a file's public surface (imports, exports,
signatures, JSDoc, decorators, module-level constants) — the same
compression philosophy `quor/pipeline/ast_summarize/python.py` already
implements for Python, mapped onto JavaScript's AST node shapes per
`docs/design/QB-005A-ast-summarization-design.md` Section 3.

Uses `tree-sitter` + `tree-sitter-javascript` (optional dependency,
`quor[javascript]` — see Section 5 of the design doc for why this is the
only viable choice: no pure-Python parser supports the sibling TypeScript
requirement QB-005D will add, so this mirrors `quor[documents]`'s
already-shipped optional-extra precedent rather than a core dependency).
`tree_sitter`/`tree_sitter_javascript` are imported **lazily, inside
`analyze_javascript()`**, not at module top level — importing this module
itself must never fail or warn just because the optional extra isn't
installed (mirrors `quor/pipeline/extract/docx.py`'s identical lazy-import
discipline). This is what lets `quor/pipeline/ast_summarize/registry.py`
register `"javascript"` **unconditionally** (no try/except at import time,
no special-casing in the router) while still failing open per-call when the
dependency is genuinely absent.

Public API: `analyze_javascript(source: str) -> set[int]` — returns the
1-indexed line numbers eligible for compression. Same return-type contract
as `analyze_python()`.

Fail-open contract — two genuinely different cases (QB-005A Section 4):
  - **Missing optional dependency:** caught here (not left to propagate),
    warns with an actionable message naming `quor[javascript]` (mirroring
    `extract_docx()`'s `ImportError` -> warn -> `None` pattern exactly,
    adapted to this module's `set[int]`-only return contract — the direct
    analog of `extract()`'s `None` is an **empty set**: from
    `code_ast_summarize.py`'s perspective, `compress_lines = analyzer(...)`
    empty -> `if not compress_lines: return mask` unchanged, the exact same
    code path already used for "no functions found," with zero changes
    needed to that file). This is a deliberate, second departure from
    QB-005A Section 4.2's original prose (which imagined this check living
    in `can_handle()`) — QB-005B already established that `can_handle()`
    has no access to `StageConfig` and moved the *unsupported-language*
    check into `apply()`/the analyzer layer instead; this extends that same
    already-shipped precedent to the *missing-dependency* case, rather than
    inventing a third mechanism.
  - **Genuine parse failure for a real `.js` file** (malformed source
    tree-sitter cannot recover from at all, or an unexpected internal
    exception): NOT caught here — propagates to `Pipeline.execute()`'s
    existing per-stage fail-open, exactly like `analyze_python()`'s
    `SyntaxError` does. In practice this is expected to be rare: unlike
    `ast.parse()`, tree-sitter is an *error-recovering* parser — malformed
    JavaScript produces a tree containing `ERROR`/`MISSING` nodes rather
    than raising, which this module handles by construction (see
    `_treesitter_utils.has_error_overlap()`), not by exception.

QB-005D: the ERROR-node-overlap exclusion machinery
(`_statement_block_interior_lines`/`_collect_error_ranges`/
`_has_error_overlap`/`_add_candidate`) moved, unmodified, to the new
`quor/pipeline/ast_summarize/_treesitter_utils.py` when `typescript.py`
needed the exact same, language-agnostic logic — see that module's own
docstring. This module's own observable behavior is unchanged by that
move (verified via before/after snapshot diff, see backlog.md's QB-005D
entry); only where the code physically lives changed.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from quor.pipeline.ast_summarize._treesitter_utils import add_candidate, collect_error_ranges

if TYPE_CHECKING:
    from tree_sitter import Node

# Node types (per tree-sitter-javascript's grammar, empirically verified
# against the installed grammar version while implementing this module —
# see backlog.md's QB-005C entry) that have a `body` field which may be a
# `statement_block` eligible for compression. Covers: a named top-level
# function, a top-level generator function, an arrow function assigned to a
# name, a (named or anonymous) function expression assigned to a name, an
# anonymous generator expression assigned to a name, and a class member
# (regular/async/generator/getter/setter/constructor — all share this one
# node type; modifiers are sibling tokens, not a different node type).
_FUNCTION_LIKE_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "arrow_function",
        "function_expression",
        "generator_function",
        "method_definition",
    }
)

# Declaration node types whose individual `variable_declarator` children may
# assign a function-like value to a name (`const foo = () => {...}`,
# `var bar = function () {...}`, `let baz = function* () {...}`).
_VARIABLE_DECLARATION_TYPES = frozenset({"lexical_declaration", "variable_declaration"})


def analyze_javascript(source: str) -> set[int]:
    """Return the 1-indexed line numbers of JavaScript function/method/
    arrow-function BODY lines eligible for compression.

    Returns an empty set (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-javascript` dependency is not installed.
    Otherwise may raise on a genuine, unrecoverable parser failure — not
    caught here, see module docstring "Fail-open".
    """
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-javascript is not installed; "
            "install quor[javascript] to enable JavaScript AST summarization "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return set()

    language = tree_sitter.Language(tree_sitter_javascript.language())
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
    """Walk `node`'s children looking for the structural constructs
    documented in QB-005A Section 3 — function/generator declarations,
    class declarations (recursing one level into their methods), variable/
    lexical declarations that assign a function-like value to a name, and
    `export`/`export default` wrappers around any of the above (unwrapped
    via the `declaration` field, then re-dispatched through this same
    function so `export function foo() {}` and a bare `function foo() {}`
    are handled identically).

    Deliberately narrow, per QB-005A's own scope: does NOT recurse into
    `if`/`try`/other conditional-block containers the way
    `quor/pipeline/ast_summarize/python.py`'s Python-specific
    `if TYPE_CHECKING:`/`try: ... except ImportError:` handling does — that
    is a Python-idiom accommodation not documented for JavaScript in the
    design's Section 3 table, and adding it here would be exactly the kind
    of undocumented, language-specific heuristic QB-005C is scoped to
    avoid. A function declared inside a top-level `if` block is therefore
    not found or compressed — see backlog.md's QB-005C entry for this
    documented limitation.

    Once a function-like node is selected for body compression, this
    function does not recurse into it any further — a function nested
    inside another function's body is implementation detail of the outer
    one, mirroring `python.py`'s identical "do not recurse into stmt.body"
    rule.
    """
    for child in node.children:
        if child.type in _FUNCTION_LIKE_TYPES:
            add_candidate(child, error_ranges, lines)
        elif child.type == "class_declaration":
            _visit_class_body(child, error_ranges, lines)
        elif child.type in _VARIABLE_DECLARATION_TYPES:
            _visit_variable_declaration(child, error_ranges, lines)
        elif child.type == "export_statement":
            declaration = child.child_by_field_name("declaration")
            if declaration is not None:
                # Re-dispatch the unwrapped declaration through the same
                # top-level logic by treating `child` itself as a
                # single-child container — avoids a second, parallel
                # dispatch table that could silently drift from the one
                # above as this function evolves.
                _visit_top_level(child, error_ranges, lines)


def _visit_class_body(class_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]) -> None:
    """Recurse one level into a class declaration's body, compressing each
    method's own body independently. Does not recurse into a method's body
    any further (same "no further recursion" rule as top-level functions).

    Class expressions assigned to a name (`const X = class { ... }`) are
    not specially recognized — only the `class Foo { ... }` declaration
    form is (see backlog.md's QB-005C entry for this documented,
    deliberate scope limitation, matching the design's own compression
    table which documents class *declarations*, not class *expressions*).
    """
    body = class_node.child_by_field_name("body")
    if body is None or body.type != "class_body":
        return
    for member in body.children:
        if member.type == "method_definition":
            add_candidate(member, error_ranges, lines)


def _visit_variable_declaration(
    decl_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]
) -> None:
    """For each `variable_declarator` in a `lexical_declaration`/
    `variable_declaration`, compress the assigned value's body if that
    value is a function-like node (arrow function, function expression, or
    generator expression)."""
    for declarator in decl_node.children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_LIKE_TYPES:
            add_candidate(value, error_ranges, lines)
