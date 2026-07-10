"""TypeScript analyzer for the AST summarization framework (QB-005D).

Compresses function/method/arrow-function **bodies** to nothing, preserving
everything that describes a file's public surface — the same compression
philosophy `python.py`/`javascript.py` already implement, mapped onto
TypeScript's AST node shapes per
`docs/design/QB-005A-ast-summarization-design.md` Section 3, plus the
TypeScript-specific declarations that section's "TypeScript (adds to JS)"
column names: `interface`, `type` alias, `enum` (all preserved **whole**,
with no body concept to compress), decorators, and — per this task's own
extended scope — `namespace`, abstract classes/methods, and overload
signatures.

Uses `tree-sitter-typescript` (optional dependency, same `quor[javascript]`
extra `tree-sitter-javascript` already uses — see "Extra name" below),
which exposes **two separate grammars**: `language_typescript()` for
`.ts` and `language_tsx()` for `.tsx`. This module exposes two public
functions, `analyze_typescript()` and `analyze_tsx()`, registered under two
separate registry keys (`"typescript"`, `"tsx"`) — mirroring how
`cat-python.toml`/`cat-javascript.toml` are two separate filters with two
separate `language` config values, not one filter that guesses. Grammar
selection is **never** inferred from file content — confirmed empirically
during implementation that JSX syntax genuinely fails to parse under the
plain `language_typescript()` grammar, and an angle-bracket type assertion
(`<number>x`) — genuinely ambiguous with a JSX element — parses fine under
it specifically because it doesn't have to disambiguate against JSX. Both
facts match QB-005A Section 8's own predicted risk exactly. Both public
functions share one internal traversal implementation
(`_analyze_with_grammar()`), and both share `_treesitter_utils.py`'s
language-agnostic ERROR-node-overlap/body-range helpers with
`javascript.py` — the design's own "one grammar API, many languages"
argument (Section 5) extends here to "one traversal shape, two grammar
variants of the same language."

**Extra name — deliberate choice, not pre-decided by the design doc:**
QB-005A Section 9 left "add `tree-sitter-typescript` to the same
`quor[javascript]` extra, or a dedicated `quor[typescript]` extra" as an
explicitly open question. Resolved here: **same `quor[javascript]` extra.**
`tree-sitter-typescript`'s wheel is small (~280 KB), a user who wants AST
compression for one of JS/TS very likely wants it for both (same
ecosystem, frequently mixed in one repo), and a second extra would only
add install-matrix permutations (`javascript` alone, `typescript` alone,
both, neither) for a dependency-weight concern that doesn't actually apply
here. `quor[javascript]` is deliberately not renamed to something more
generic — renaming an existing public extra name is a bigger, breaking
change for existing installs, out of proportion to this decision.

Public API: `analyze_typescript(source: str) -> set[int]`,
`analyze_tsx(source: str) -> set[int]`. Same return-type contract as
`analyze_python()`/`analyze_javascript()`.

Fail-open contract — identical in shape to `javascript.py`'s (see that
module's own docstring for the full reasoning, not repeated here):
missing `tree-sitter`/`tree-sitter-typescript` is caught locally, warns
with an actionable `quor[javascript]` message, and returns an empty set;
a genuine unrecoverable parser failure is not caught here and propagates
to `Pipeline.execute()`'s existing per-stage fail-open.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from quor.pipeline.ast_summarize._treesitter_utils import add_candidate, collect_error_ranges

if TYPE_CHECKING:
    from tree_sitter import Node

# Node types with a `body` field that may be a `statement_block` eligible
# for compression — the TypeScript superset of javascript.py's own
# _FUNCTION_LIKE_TYPES (empirically verified against the installed grammar
# during implementation, see backlog.md's QB-005D entry):
#   - function_declaration / generator_function_declaration / arrow_function
#     / function_expression / generator_function / method_definition: same
#     six types javascript.py already recognizes, unchanged meaning.
#   - function_signature: an overload signature (`function f(x: number):
#     number;`, no implementation) — has no `body` field at all, so this is
#     always a no-op for compression purposes, included explicitly for
#     self-documentation ("we thought about overloads; here's why they're
#     inert") rather than relying on silent omission.
#   - abstract_method_signature: an abstract method inside an
#     abstract/interface body (`abstract area(): number;`) — likewise
#     always body-less, included for the same explicit-documentation reason.
_FUNCTION_LIKE_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "arrow_function",
        "function_expression",
        "generator_function",
        "method_definition",
        "function_signature",
        "abstract_method_signature",
    }
)

# class_declaration: plain TS classes, identical node type to JS.
# abstract_class_declaration: `abstract class Foo { ... }` — a genuinely
# distinct top-level node type in tree-sitter-typescript's grammar (not a
# modifier on class_declaration), empirically confirmed during
# implementation. Both expose the same `body` field shape (class_body).
_CLASS_LIKE_TYPES = frozenset({"class_declaration", "abstract_class_declaration"})

# Declaration node types whose individual `variable_declarator` children may
# assign a function-like value to a name — identical to javascript.py.
_VARIABLE_DECLARATION_TYPES = frozenset({"lexical_declaration", "variable_declaration"})

# TypeScript-specific declarations that are preserved **whole** by
# deliberate omission from the dispatch table below, not by any special
# "preserve" code path — see module docstring's "TypeScript-specific
# handling" note in backlog.md for the full reasoning. Listed here as an
# explicit, documented inventory (not asserted anywhere at runtime) so a
# future reader can see this was a considered decision, not an oversight:
#   - interface_declaration — no body concept at all (interface_body holds
#     only property_signature/method_signature members, verified to never
#     contain a statement_block node).
#   - type_alias_declaration, enum_declaration — no function-like content.
#   - `namespace X { ... }` / `module X { ... }` — parses as
#     expression_statement wrapping an internal_module node (an
#     empirically-confirmed grammar quirk, not documented anywhere in
#     tree-sitter-typescript's own public docs at implementation time).
#     Deliberately NOT recursed into: this task's own scope note ("namespace
#     — if covered by the grammar") groups it with interface/type/enum as a
#     "preserve" category, and nothing in QB-005A's Section 3 table
#     documents recursing into a namespace body to compress nested function
#     declarations — doing so anyway would be exactly the kind of
#     undocumented, language-specific heuristic this task's own instructions
#     warn against ("avoid language-specific heuristics unless explicitly
#     documented"). A function declared inside a namespace is therefore
#     preserved in full, not compressed — a conservative, documented
#     limitation, not a bug.
_TYPESCRIPT_WHOLE_PRESERVED_NODE_TYPES_FOR_REFERENCE = frozenset(
    {"interface_declaration", "type_alias_declaration", "enum_declaration", "expression_statement"}
)


def analyze_typescript(source: str) -> set[int]:
    """Return the 1-indexed line numbers of TypeScript (`.ts`) function/
    method/arrow-function BODY lines eligible for compression, using the
    plain `language_typescript()` grammar (no JSX support — see
    `analyze_tsx()` for `.tsx`).

    Fail-open contract identical to `analyze_javascript()` — see this
    module's own docstring.
    """
    return _analyze_with_grammar(source, tsx=False)


def analyze_tsx(source: str) -> set[int]:
    """Return the 1-indexed line numbers of TSX (`.tsx`) function/method/
    arrow-function BODY lines eligible for compression, using the
    `language_tsx()` grammar (JSX support, at the cost of the angle-bracket
    type-assertion syntax `<T>x` that only the plain TypeScript grammar
    supports unambiguously — see module docstring).

    Fail-open contract identical to `analyze_javascript()` — see this
    module's own docstring.
    """
    return _analyze_with_grammar(source, tsx=True)


def _analyze_with_grammar(source: str, *, tsx: bool) -> set[int]:
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-typescript is not installed; "
            "install quor[javascript] to enable TypeScript AST summarization "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return set()

    grammar = (
        tree_sitter_typescript.language_tsx()
        if tsx
        else tree_sitter_typescript.language_typescript()
    )
    language = tree_sitter.Language(grammar)
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
    documented in QB-005A Section 3 plus this task's TypeScript-specific
    extensions (abstract classes, overload/abstract-method signatures) —
    function/generator declarations, class declarations (recursing one
    level into their members), variable/lexical declarations that assign a
    function-like value to a name, and `export`/`export default` wrappers
    around any of the above (unwrapped via the `declaration` field, then
    re-dispatched through this same function — identical shape to
    `javascript.py`'s own export handling).

    Every TypeScript-only declaration this task asks to "preserve"
    (`interface`, `type` alias, `enum`, `namespace`) is preserved by
    deliberate omission from this dispatch table, not by a special-case
    branch — see `_TYPESCRIPT_WHOLE_PRESERVED_NODE_TYPES_FOR_REFERENCE`'s
    own comment above for why, and for why a namespace's contents are not
    recursed into even though it does have executable content.

    Once a function-like node is selected for body compression, this
    function does not recurse into it any further — identical rule to
    `javascript.py`/`python.py`.
    """
    for child in node.children:
        if child.type in _FUNCTION_LIKE_TYPES:
            add_candidate(child, error_ranges, lines)
        elif child.type in _CLASS_LIKE_TYPES:
            _visit_class_body(child, error_ranges, lines)
        elif child.type in _VARIABLE_DECLARATION_TYPES:
            _visit_variable_declaration(child, error_ranges, lines)
        elif child.type == "export_statement":
            declaration = child.child_by_field_name("declaration")
            if declaration is not None:
                _visit_top_level(child, error_ranges, lines)


def _visit_class_body(class_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]) -> None:
    """Recurse one level into a class (or abstract class) declaration's
    body, compressing each member's own body independently where one
    exists. `abstract_method_signature` members (no body field) and
    `function_signature`-shaped overloads are included in the same
    `_FUNCTION_LIKE_TYPES` check as real methods — harmless no-ops, per
    that set's own comment.

    Class *expressions* assigned to a name (`const X = class { ... }`) are
    not specially recognized — identical, deliberate scope limitation to
    `javascript.py`'s own.
    """
    body = class_node.child_by_field_name("body")
    if body is None or body.type != "class_body":
        return
    for member in body.children:
        if member.type in _FUNCTION_LIKE_TYPES:
            add_candidate(member, error_ranges, lines)


def _visit_variable_declaration(
    decl_node: Node, error_ranges: list[tuple[int, int]], lines: set[int]
) -> None:
    """For each `variable_declarator` in a `lexical_declaration`/
    `variable_declaration`, compress the assigned value's body if that
    value is a function-like node. Identical to `javascript.py`'s own —
    a TypeScript type annotation on the declarator (`const f: Handler =
    ...`) lives in a separate field this function never touches, so it
    doesn't change this lookup at all."""
    for declarator in decl_node.children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_LIKE_TYPES:
            add_candidate(value, error_ranges, lines)
