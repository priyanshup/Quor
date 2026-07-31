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

from quor.pipeline.ast_summarize._treesitter_utils import (
    add_candidate,
    collect_error_ranges,
    extract_es_import_statements,
    iter_descendants,
)
from quor.pipeline.ast_summarize.import_collapse import collapse_import_runs
from quor.pipeline.ast_summarize.import_model import ImportBlockReplacement
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import ENTRY_POINT_NAMES, Symbol, SymbolKind

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

# QB-066 symbol extraction — same split as javascript.py's own
# _FUNCTION_DECLARATION_TYPES/_FUNCTION_VALUE_TYPES, extended with the
# signature-only node types the compression side already tracks in
# _FUNCTION_LIKE_TYPES above (harmless here too: a signature has no `name`
# field of its own to report distinctly from its enclosing declaration, so
# it's naturally never reached as a top-level declaration node).
_FUNCTION_DECLARATION_TYPES = frozenset({"function_declaration", "generator_function_declaration"})
_FUNCTION_VALUE_TYPES = frozenset({"arrow_function", "function_expression", "generator_function"})

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


def collapse_imports_typescript(source: str) -> list[ImportBlockReplacement]:
    """Return the collapsed replacement for every token-cheaper run of
    consecutive top-level `import` statements in a `.ts` file (QB-096),
    using the plain `language_typescript()` grammar — see
    `import_collapse.py`'s module docstring for the shared run-detection,
    rendering, and cost-gate rules this delegates to. Fail-open contract
    identical to `analyze_typescript()`."""
    return _collapse_imports_with_grammar(source, tsx=False)


def collapse_imports_tsx(source: str) -> list[ImportBlockReplacement]:
    """Return the collapsed replacement for every token-cheaper run of
    consecutive top-level `import` statements in a `.tsx` file (QB-096),
    using the `language_tsx()` grammar. Fail-open contract identical to
    `analyze_tsx()`."""
    return _collapse_imports_with_grammar(source, tsx=True)


def _collapse_imports_with_grammar(source: str, *, tsx: bool) -> list[ImportBlockReplacement]:
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-typescript is not installed; "
            "install quor[javascript] to enable TypeScript import collapsing "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return []

    grammar = (
        tree_sitter_typescript.language_tsx() if tsx else tree_sitter_typescript.language_typescript()
    )
    language = tree_sitter.Language(grammar)
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    statements = extract_es_import_statements(tree.root_node)
    if not statements:
        return []
    return collapse_import_runs(statements, source.split("\n"), comment_prefix="//")


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


def extract_symbols_typescript(source: str) -> list[Symbol]:
    """Return every top-level class/interface/enum/function declaration and
    each class's direct methods, parsed with the plain `.ts` grammar
    (QB-066). See `extract_symbols_tsx()` for `.tsx`; both share
    `_extract_symbols_with_grammar()`."""
    return _extract_symbols_with_grammar(source, tsx=False)


def extract_symbols_tsx(source: str) -> list[Symbol]:
    """Return every top-level class/interface/enum/function declaration and
    each class's direct methods, parsed with the `.tsx` (JSX-aware) grammar
    (QB-066). See `extract_symbols_typescript()` for `.ts`."""
    return _extract_symbols_with_grammar(source, tsx=True)


def _extract_symbols_with_grammar(source: str, *, tsx: bool) -> list[Symbol]:
    """`is_public` conventions (QB-066): a top-level declaration's
    `is_public` reflects `export` — identical to `javascript.py`'s own
    reasoning. A class member's `is_public` reflects TypeScript's explicit
    `accessibility_modifier` (`public`/`private`/`protected`) when present;
    TypeScript's own default for an unmarked member is public, so a member
    with no modifier at all is `is_public=True` — unlike JavaScript's
    `#name`-only privacy mechanism, which `javascript.py` still applies
    here too (a `#name` member is private regardless of any modifier, since
    the two mechanisms are orthogonal in real TypeScript).

    Returns an empty list (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-typescript` dependency is not installed.
    Otherwise may raise on a genuine, unrecoverable parser failure — not
    caught here, same fail-open contract as `_analyze_with_grammar()`.
    """
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-typescript is not installed; "
            "install quor[javascript] to enable TypeScript symbol extraction "
            "(falling back to no symbols for this file)",
            stacklevel=2,
        )
        return []

    grammar = (
        tree_sitter_typescript.language_tsx() if tsx else tree_sitter_typescript.language_typescript()
    )
    language = tree_sitter.Language(grammar)
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    symbols: list[Symbol] = []
    _visit_top_level_symbols(tree.root_node, symbols, is_exported=False)
    return symbols


def _decode(node: Node) -> str:
    text = node.text
    return text.decode("utf-8") if text is not None else ""


def _visit_top_level_symbols(node: Node, symbols: list[Symbol], *, is_exported: bool) -> None:
    for child in node.children:
        if child.type in _FUNCTION_DECLARATION_TYPES:
            _add_function_symbol(child, symbols, is_public=is_exported)
        elif child.type in _CLASS_LIKE_TYPES:
            _add_class_symbol(child, symbols, is_public=is_exported)
        elif child.type == "interface_declaration":
            _add_named_symbol(child, symbols, kind="interface", is_public=is_exported)
        elif child.type == "enum_declaration":
            _add_named_symbol(child, symbols, kind="enum", is_public=is_exported)
        elif child.type in _VARIABLE_DECLARATION_TYPES:
            _add_variable_function_symbols(child, symbols, is_public=is_exported)
        elif child.type == "export_statement":
            declaration = child.child_by_field_name("declaration")
            if declaration is not None:
                _visit_top_level_symbols(child, symbols, is_exported=True)


def _add_named_symbol(node: Node, symbols: list[Symbol], *, kind: SymbolKind, is_public: bool) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    symbols.append(
        Symbol(name=_decode(name_node), kind=kind, line=node.start_point.row + 1, is_public=is_public)
    )


def _add_function_symbol(node: Node, symbols: list[Symbol], *, is_public: bool) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    name = _decode(name_node)
    symbols.append(
        Symbol(
            name=name,
            kind="function",
            line=node.start_point.row + 1,
            is_public=is_public,
            is_entry_point=name in ENTRY_POINT_NAMES,
        )
    )


def _add_class_symbol(node: Node, symbols: list[Symbol], *, is_public: bool) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    symbols.append(
        Symbol(name=_decode(name_node), kind="class", line=node.start_point.row + 1, is_public=is_public)
    )
    body = node.child_by_field_name("body")
    if body is None or body.type != "class_body":
        return
    for member in body.children:
        if member.type == "method_definition":
            _add_method_symbol(member, symbols)


def _add_method_symbol(node: Node, symbols: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None or name_node.type not in ("property_identifier", "private_property_identifier"):
        return
    name = _decode(name_node)
    is_private_field = name_node.type == "private_property_identifier"
    has_non_public_modifier = any(
        child.type == "accessibility_modifier" and _decode(child) in ("private", "protected")
        for child in node.children
    )
    symbols.append(
        Symbol(
            name=name,
            kind="method",
            line=node.start_point.row + 1,
            is_public=not is_private_field and not has_non_public_modifier,
            is_entry_point=name in ENTRY_POINT_NAMES,
        )
    )


def _add_variable_function_symbols(decl_node: Node, symbols: list[Symbol], *, is_public: bool) -> None:
    for declarator in decl_node.children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is None or value.type not in _FUNCTION_VALUE_TYPES:
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        name = _decode(name_node)
        symbols.append(
            Symbol(
                name=name,
                kind="function",
                line=declarator.start_point.row + 1,
                is_public=is_public,
                is_entry_point=name in ENTRY_POINT_NAMES,
            )
        )


def extract_relationships_typescript(source: str) -> list[Relationship]:
    """Return every deterministic import/export/inherits/
    implements_interface/overrides/calls relationship in a `.ts` file
    (QB-067), using the plain `language_typescript()` grammar. See
    `extract_relationships_tsx()` for `.tsx`; both share
    `_extract_relationships_with_grammar()`."""
    return _extract_relationships_with_grammar(source, tsx=False)


def extract_relationships_tsx(source: str) -> list[Relationship]:
    """Return every deterministic import/export/inherits/
    implements_interface/overrides/calls relationship in a `.tsx` file
    (QB-067), using the `language_tsx()` (JSX-aware) grammar. See
    `extract_relationships_typescript()` for `.ts`."""
    return _extract_relationships_with_grammar(source, tsx=True)


def _extract_relationships_with_grammar(source: str, *, tsx: bool) -> list[Relationship]:
    """File-local and unresolved — see `relationship_model.py`'s module
    docstring for why cross-file resolution is not this function's job.

    - **imports/export**: identical grammar shape and semantics to
      `extract_relationships_javascript()` (`import_statement`/
      `export_statement`/`export_clause`) — TypeScript adds no new import/
      export syntax this function needs to special-case.
    - **inherits**: a class's `extends` clause (`class Foo extends Base`),
      recorded the same way as JavaScript's.
    - **implements_interface**: a class's `implements` clause (`class Foo
      implements A, B<T>`) — TypeScript's one syntactically unambiguous
      addition over JavaScript (see module docstring's own "extends vs.
      implements" distinction). `interface Foo extends Bar, Baz` is also
      reported as `implements_interface` (not `inherits`) — an interface
      extending another interface is structurally a supertype
      *requirement*, the same relationship `implements` expresses for a
      class, not a class-inheritance chain the way JS's `extends` is
      (Python's own "no separate interface concept" note in `python.py`
      does not apply here — TypeScript's interface/class distinction is
      real and syntactic).
    - **overrides**: a class method carrying TypeScript 4.3+'s `override`
      modifier (`override method() {}}`, its own distinct grammar node,
      empirically confirmed present/absent per method) — `qualifier` is
      the class's own raw `extends` target name (the type the override is
      claimed against); a method with no `override` modifier is not
      reported, even if a same-named method exists on a base class — no
      guessing beyond the explicit keyword.
    - **calls**: identical scope and shape to
      `extract_relationships_javascript()`'s (only from within a `Symbol`-
      reported function/method, bare/`.this`/single-level-object calls
      only).

    Returns an empty list (with an actionable warning) if the optional
    dependency is missing. Otherwise may raise on a genuine, unrecoverable
    parser failure — same fail-open contract as `_analyze_with_grammar()`.
    """
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-typescript is not installed; "
            "install quor[javascript] to enable TypeScript relationship extraction "
            "(falling back to no relationships for this file)",
            stacklevel=2,
        )
        return []

    grammar = (
        tree_sitter_typescript.language_tsx() if tsx else tree_sitter_typescript.language_typescript()
    )
    language = tree_sitter.Language(grammar)
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    relationships: list[Relationship] = []
    _visit_top_level_relationships(tree.root_node, relationships)
    return relationships


def _string_source_text(node: Node) -> str:
    for child in node.children:
        if child.type == "string_fragment":
            return _decode(child)
    return ""


def _type_name(node: Node) -> str | None:
    """A plain identifier/type name, a single-level-or-deeper dotted
    reference, or a generic instantiation's own base name (`Comparable<T>`
    -> `"Comparable"`) — `None` for any other expression shape, never
    guessed."""
    if node.type in ("identifier", "type_identifier"):
        return _decode(node)
    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if obj is not None and prop is not None:
            base = _type_name(obj)
            return f"{base}.{_decode(prop)}" if base is not None else None
        return None
    if node.type == "nested_type_identifier":
        module = node.child_by_field_name("module")
        name = node.child_by_field_name("name")
        if module is not None and name is not None:
            base = _type_name(module)
            return f"{base}.{_decode(name)}" if base is not None else None
        return None
    if node.type == "generic_type":
        name_node = node.child_by_field_name("name")
        return _type_name(name_node) if name_node is not None else None
    return None


def _class_heritage_bases(heritage: Node) -> tuple[Node | None, list[Node]]:
    """Return `(extends_value_node, implements_type_nodes)` for a
    `class_heritage` node under `typescript.py`'s grammar — `extends_clause`
    (field `value`) and/or `implements_clause` (named children only) are
    each optional, explicit, and syntactically distinct (see module
    docstring's "extends vs. implements" note); unlike `javascript.py`'s
    plain-JS grammar, which has no `implements_clause` at all."""
    extends_node: Node | None = None
    implements_nodes: list[Node] = []
    for child in heritage.children:
        if child.type == "extends_clause":
            extends_node = child.child_by_field_name("value")
        elif child.type == "implements_clause":
            implements_nodes = [c for c in child.children if c.is_named]
    return extends_node, implements_nodes


def _visit_top_level_relationships(node: Node, relationships: list[Relationship]) -> None:
    for child in node.children:
        if child.type == "import_statement":
            _add_import_relationships(child, relationships)
        elif child.type in _FUNCTION_DECLARATION_TYPES:
            _add_calls_for_function(child, relationships)
        elif child.type in _CLASS_LIKE_TYPES:
            _add_class_relationships(child, relationships)
        elif child.type == "interface_declaration":
            _add_interface_relationships(child, relationships)
        elif child.type in _VARIABLE_DECLARATION_TYPES:
            _add_variable_function_relationships(child, relationships)
        elif child.type == "export_statement":
            _add_export_statement_relationships(child, relationships)


def _add_import_relationships(node: Node, relationships: list[Relationship]) -> None:
    source_node = node.child_by_field_name("source")
    if source_node is None:
        return
    target = _string_source_text(source_node)
    line = node.start_point.row + 1

    clause = next((c for c in node.children if c.type == "import_clause"), None)
    if clause is None:
        relationships.append(Relationship(kind="import", source="", target=target, line=line))
        return

    for child in clause.children:
        if child.type == "identifier":
            relationships.append(
                Relationship(kind="import", source="", target=target, line=line, qualifier=_decode(child))
            )
        elif child.type == "namespace_import":
            name_node = next((c for c in child.children if c.type == "identifier"), None)
            if name_node is not None:
                relationships.append(
                    Relationship(
                        kind="import", source="", target=target, line=line, qualifier=_decode(name_node)
                    )
                )
        elif child.type == "named_imports":
            for specifier in child.children:
                if specifier.type != "import_specifier":
                    continue
                name_node = specifier.child_by_field_name("name")
                if name_node is None:
                    continue
                alias_node = specifier.child_by_field_name("alias")
                local_name = _decode(alias_node) if alias_node is not None else _decode(name_node)
                relationships.append(
                    Relationship(
                        kind="import",
                        source="",
                        target=target,
                        line=line,
                        qualifier=local_name,
                        origin=_decode(name_node),
                    )
                )


def _exported_name_for_declaration(declaration: Node) -> str | None:
    if declaration.type in _FUNCTION_DECLARATION_TYPES or declaration.type in _CLASS_LIKE_TYPES:
        name_node = declaration.child_by_field_name("name")
        return _decode(name_node) if name_node is not None else None
    if declaration.type == "interface_declaration" or declaration.type == "enum_declaration":
        name_node = declaration.child_by_field_name("name")
        return _decode(name_node) if name_node is not None else None
    if declaration.type in _VARIABLE_DECLARATION_TYPES:
        name_nodes = [
            d.child_by_field_name("name") for d in declaration.children if d.type == "variable_declarator"
        ]
        names = [_decode(n) for n in name_nodes if n is not None]
        return names[0] if len(names) == 1 else None
    return None


def _add_export_statement_relationships(node: Node, relationships: list[Relationship]) -> None:
    line = node.start_point.row + 1
    source_node = node.child_by_field_name("source")
    re_export_target = _string_source_text(source_node) if source_node is not None else ""

    export_clause = next((c for c in node.children if c.type == "export_clause"), None)
    if export_clause is not None:
        for specifier in export_clause.children:
            if specifier.type != "export_specifier":
                continue
            name_node = specifier.child_by_field_name("name")
            if name_node is None:
                continue
            alias_node = specifier.child_by_field_name("alias")
            public_name = _decode(alias_node) if alias_node is not None else _decode(name_node)
            relationships.append(
                Relationship(kind="export", source=public_name, target=re_export_target, line=line)
            )
        return

    declaration = node.child_by_field_name("declaration")
    if declaration is not None:
        name = _exported_name_for_declaration(declaration)
        if name is not None:
            relationships.append(Relationship(kind="export", source=name, target="", line=line))
        # Re-dispatch so the declaration's own inherits/implements/calls are still found.
        _visit_top_level_relationships(node, relationships)


def _add_class_relationships(node: Node, relationships: list[Relationship]) -> None:
    name_node = node.child_by_field_name("name")
    class_name = _decode(name_node) if name_node is not None else None

    superclass_name: str | None = None
    heritage = next((c for c in node.children if c.type == "class_heritage"), None)
    if heritage is not None and class_name is not None:
        extends_node, implements_nodes = _class_heritage_bases(heritage)
        if extends_node is not None:
            base_name = _type_name(extends_node)
            if base_name is not None:
                superclass_name = base_name
                relationships.append(
                    Relationship(
                        kind="inherits",
                        source=class_name,
                        target=base_name,
                        line=extends_node.start_point.row + 1,
                    )
                )
        for type_node in implements_nodes:
            interface_name = _type_name(type_node)
            if interface_name is not None:
                relationships.append(
                    Relationship(
                        kind="implements_interface",
                        source=class_name,
                        target=interface_name,
                        line=type_node.start_point.row + 1,
                    )
                )

    body = node.child_by_field_name("body")
    if body is None or body.type != "class_body":
        return
    for member in body.children:
        if member.type != "method_definition":
            continue
        member_name_node = member.child_by_field_name("name")
        if member_name_node is None or member_name_node.type not in (
            "property_identifier",
            "private_property_identifier",
        ):
            continue
        method_name = _decode(member_name_node)
        _collect_calls(member, method_name, relationships)
        if superclass_name is not None and any(c.type == "override_modifier" for c in member.children):
            relationships.append(
                Relationship(
                    kind="overrides",
                    source=method_name,
                    target=method_name,
                    line=member.start_point.row + 1,
                    qualifier=superclass_name,
                )
            )


def _add_interface_relationships(node: Node, relationships: list[Relationship]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    interface_name = _decode(name_node)
    for child in node.children:
        if child.type != "extends_type_clause":
            continue
        for type_node in child.children:
            if not type_node.is_named:
                continue
            base_name = _type_name(type_node)
            if base_name is not None:
                relationships.append(
                    Relationship(
                        kind="implements_interface",
                        source=interface_name,
                        target=base_name,
                        line=type_node.start_point.row + 1,
                    )
                )


def _add_variable_function_relationships(node: Node, relationships: list[Relationship]) -> None:
    for declarator in node.children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is None or value.type not in _FUNCTION_VALUE_TYPES:
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue
        _collect_calls(value, _decode(name_node), relationships)


def _add_calls_for_function(node: Node, relationships: list[Relationship]) -> None:
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
        elif func.type == "member_expression":
            obj = func.child_by_field_name("object")
            prop = func.child_by_field_name("property")
            if prop is None or prop.type != "property_identifier":
                continue
            if obj is not None and obj.type == "this":
                relationships.append(
                    Relationship(
                        kind="calls", source=source_name, target=_decode(prop), line=line, qualifier="this"
                    )
                )
            elif obj is not None and obj.type == "identifier":
                relationships.append(
                    Relationship(
                        kind="calls",
                        source=source_name,
                        target=_decode(prop),
                        line=line,
                        qualifier=_decode(obj),
                    )
                )
