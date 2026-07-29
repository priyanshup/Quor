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

from quor.pipeline.ast_summarize._treesitter_utils import (
    add_candidate,
    collect_error_ranges,
    iter_descendants,
)
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import ENTRY_POINT_NAMES, Symbol

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

# Top-level named-function declaration node types (QB-066 symbol
# extraction) — the subset of _FUNCTION_LIKE_TYPES that can appear directly
# as a top-level declaration (arrow_function/function_expression/
# generator_function only ever appear as a *value*, handled instead via
# _VARIABLE_DECLARATION_TYPES below; method_definition only ever appears
# inside a class_body).
_FUNCTION_DECLARATION_TYPES = frozenset({"function_declaration", "generator_function_declaration"})

# Function-like *value* node types a variable_declarator may assign (QB-066)
# — excludes method_definition/the two _declaration types above, which are
# never a declarator's value.
_FUNCTION_VALUE_TYPES = frozenset({"arrow_function", "function_expression", "generator_function"})


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


def extract_symbols_javascript(source: str) -> list[Symbol]:
    """Return every top-level function/class declaration and each class's
    direct methods (QB-066), in source order.

    A top-level declaration's `is_public` reflects whether it is directly
    `export`ed (`export function foo() {}`, `export class Foo {}`,
    `export const bar = () => {}`) — JavaScript's only real module-level
    visibility mechanism, since a bare top-level `const`/`function` is
    reachable from every other module in the same file but not importable
    from outside it. A class method's `is_public` instead reflects ES2022
    private-field syntax (`#name`) — the language's only real member-level
    privacy mechanism — independent of whether the enclosing class itself
    is exported.

    Anonymous declarations (`export default function() {}`, `export
    default class {}`) and computed method names (`[Symbol.iterator]()
    {}`) have no deterministic simple name and are silently omitted, not
    guessed at.

    Returns an empty list (with the same actionable warning
    `analyze_javascript()` emits) if the optional `tree-sitter`/
    `tree-sitter-javascript` dependency is not installed. Otherwise may
    raise on a genuine, unrecoverable parser failure — not caught here,
    same fail-open contract as `analyze_javascript()` (see module
    docstring's "Fail-open" section)."""
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-javascript is not installed; "
            "install quor[javascript] to enable JavaScript symbol extraction "
            "(falling back to no symbols for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_javascript.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    symbols: list[Symbol] = []
    _visit_top_level_symbols(tree.root_node, symbols, is_exported=False)
    return symbols


def _decode(node: Node) -> str:
    text = node.text
    return text.decode("utf-8") if text is not None else ""


def _visit_top_level_symbols(node: Node, symbols: list[Symbol], *, is_exported: bool) -> None:
    """Mirrors `_visit_top_level()`'s own child-scanning shape (including
    the `export_statement` re-dispatch trick), collecting `Symbol` entries
    instead of compressible line ranges."""
    for child in node.children:
        if child.type in _FUNCTION_DECLARATION_TYPES:
            _add_function_symbol(child, symbols, is_public=is_exported)
        elif child.type == "class_declaration":
            _add_class_symbol(child, symbols, is_public=is_exported)
        elif child.type in _VARIABLE_DECLARATION_TYPES:
            _add_variable_function_symbols(child, symbols, is_public=is_exported)
        elif child.type == "export_statement":
            declaration = child.child_by_field_name("declaration")
            if declaration is not None:
                _visit_top_level_symbols(child, symbols, is_exported=True)


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
    symbols.append(
        Symbol(
            name=name,
            kind="method",
            line=node.start_point.row + 1,
            is_public=name_node.type != "private_property_identifier",
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


def extract_relationships_javascript(source: str) -> list[Relationship]:
    """Return every deterministic import/export/inherits/calls relationship
    JavaScript's grammar makes explicit (QB-067) — file-local and
    unresolved, see `relationship_model.py`'s module docstring.

    - **imports**: every `import_statement` — a default import
      (`qualifier=<local name>`), each named import (`{a, b as c}`,
      `origin=<original name>`, `qualifier=<local/alias name>`), a
      namespace import (`* as ns`, `qualifier=<ns name>`), and a bare
      side-effect import (`import './x'`, `qualifier=None`) are each their
      own `Relationship`. `require('./x')` (CommonJS) is deliberately NOT
      treated as an import here — it is an ordinary call expression (still
      reported as a `calls` relationship with `target="require"`), not a
      static, syntactically-guaranteed dependency the way ESM `import` is;
      resolving it would need to track which variable a `require()`
      result gets assigned to, a heuristic this task rules out.
    - **export**: a re-export (`export { x } from './mod'`,
      `target=<module>`) and a local export (`export { x, y as z }`,
      `export function foo() {}`, `export const bar = ...`) are both
      covered — `source` is always the exported (public) name. `export
      default <anonymous>` has no deterministic name and is skipped, not
      guessed, mirroring `extract_symbols_javascript()`'s identical
      anonymous-default-export omission.
    - **inherits**: a `class Foo extends Base` (or `extends ns.Base`,
      recorded as `"ns.Base"`). JavaScript has no `implements`/interface
      syntax at all — every resolvable base is `inherits`, never
      `implements_interface` (see `typescript.py` for where that
      distinction becomes real).
    - **overrides**: not emitted — JavaScript has no syntactic override
      marker (see `typescript.py`'s `override` modifier for where one
      exists).
    - **calls**: only from within a function/method already reported as a
      `Symbol` by `extract_symbols_javascript()` (top-level function
      declarations, class methods, and a variable assigned a function-like
      value) — `source` is that `Symbol`'s own name. A call's callee is
      recorded only when it is a bare identifier (`qualifier=None`) or a
      single-level `object.property()` access where `object` is itself a
      bare identifier or `this` (`qualifier="this"`/`qualifier=<name>`) —
      a deeper chain (`a.b.c()`) is skipped, not guessed.

    Returns an empty list (with the same actionable warning
    `analyze_javascript()` emits) if the optional dependency is missing.
    Otherwise may raise on a genuine, unrecoverable parser failure — same
    fail-open contract as `analyze_javascript()`."""
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-javascript is not installed; "
            "install quor[javascript] to enable JavaScript relationship extraction "
            "(falling back to no relationships for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_javascript.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    relationships: list[Relationship] = []
    _visit_top_level_relationships(tree.root_node, relationships)
    return relationships


def _string_source_text(node: Node) -> str:
    """Decode a `string` node's inner text (between its quote tokens) —
    empty for an empty string literal, whose `string` node has no
    `string_fragment` child at all."""
    for child in node.children:
        if child.type == "string_fragment":
            return _decode(child)
    return ""


def _type_name(node: Node) -> str | None:
    """A plain identifier/type name, or a single-level-or-deeper dotted
    reference (`ns.Base`) — `None` for any other expression shape (a call,
    a generic instantiation with an unrecognized base, ...), never
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


def _class_heritage_bases(heritage: Node) -> tuple[Node | None, list[Node]]:
    """Return `(extends_value_node, implements_type_nodes)` for a
    `class_heritage` node. Plain JS's grammar makes `class_heritage`'s own
    child the base expression directly (no `extends_clause` wrapper, no
    `implements_clause` at all — JS has no `implements` keyword);
    `typescript.py`'s grammar wraps the same information in explicit
    `extends_clause`/`implements_clause` children instead (empirically
    verified against both installed grammars) — this function handles the
    plain-JS shape only; `typescript.py` has its own version for its own
    grammar shape."""
    extends_node: Node | None = None
    for child in heritage.children:
        if child.is_named:
            extends_node = child
    return extends_node, []


def _visit_top_level_relationships(node: Node, relationships: list[Relationship]) -> None:
    for child in node.children:
        if child.type == "import_statement":
            _add_import_relationships(child, relationships)
        elif child.type in _FUNCTION_DECLARATION_TYPES:
            _add_calls_for_function(child, relationships)
        elif child.type == "class_declaration":
            _add_class_relationships(child, relationships)
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
    if declaration.type in _FUNCTION_DECLARATION_TYPES or declaration.type == "class_declaration":
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
        # Re-dispatch so the declaration's own inherits/calls are still found.
        _visit_top_level_relationships(node, relationships)


def _add_class_relationships(node: Node, relationships: list[Relationship]) -> None:
    name_node = node.child_by_field_name("name")
    class_name = _decode(name_node) if name_node is not None else None

    heritage = next((c for c in node.children if c.type == "class_heritage"), None)
    if heritage is not None and class_name is not None:
        extends_node, _implements_nodes = _class_heritage_bases(heritage)
        if extends_node is not None:
            base_name = _type_name(extends_node)
            if base_name is not None:
                relationships.append(
                    Relationship(
                        kind="inherits",
                        source=class_name,
                        target=base_name,
                        line=extends_node.start_point.row + 1,
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
        _collect_calls(member, _decode(member_name_node), relationships)


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
