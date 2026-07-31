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

from quor.pipeline.ast_summarize._treesitter_utils import (
    add_candidate,
    collect_error_ranges,
    iter_descendants,
)
from quor.pipeline.ast_summarize.import_collapse import collapse_import_runs
from quor.pipeline.ast_summarize.import_model import (
    ImportBlockReplacement,
    ImportedName,
    ImportStatement,
)
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.ast_summarize.symbol_model import ENTRY_POINT_NAMES, Symbol, SymbolKind

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


# Top-level type-declaration node types (QB-066 symbol extraction) mapped to
# the Symbol kind each represents, and to the class-like body node type
# `_visit_type_body()`-equivalent traversal should expect — extends
# `_CLASS_LIKE_BODY_TYPES` above (class/interface only, for compression
# purposes) with `enum_declaration`, which the compression side deliberately
# excludes (see module docstring) but which symbol extraction must report
# since "Enums" is one of this feature's required categories. Enum members
# are not themselves visited (no method/field body to compress or report).
_TYPE_DECLARATION_KINDS: dict[str, SymbolKind] = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
}

# Member node types that count as a "method" symbol — a real method
# (with or without a body: an interface method signature has none) and a
# constructor, both callable members.
_METHOD_LIKE_SYMBOL_TYPES = frozenset({"method_declaration", "constructor_declaration"})


def extract_symbols_java(source: str) -> list[Symbol]:
    """Return every top-level class/interface/enum declaration and each
    class/interface's direct methods/constructors (QB-066), in source
    order. A member nested inside another type's body (a member class,
    interface, or enum) is not itself visited — the same one-level scope
    boundary `_visit_type_body()` already applies for compression.

    `is_public` reflects an explicit `public` modifier — Java's own
    default for an unmarked top-level type or member is package-private,
    not public, so an unmarked declaration is `is_public=False`.

    Returns an empty list (with an actionable warning) if the optional
    `tree-sitter`/`tree-sitter-java` dependency is not installed. Otherwise
    may raise on a genuine, unrecoverable parser failure — not caught here,
    same fail-open contract as `analyze_java()`."""
    try:
        import tree_sitter
        import tree_sitter_java
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-java is not installed; "
            "install quor[java] to enable Java symbol extraction "
            "(falling back to no symbols for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_java.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    symbols: list[Symbol] = []
    for child in tree.root_node.children:
        kind = _TYPE_DECLARATION_KINDS.get(child.type)
        if kind is not None:
            _add_type_symbol(child, symbols, kind=kind)
    return symbols


def _decode(node: Node) -> str:
    text = node.text
    return text.decode("utf-8") if text is not None else ""


def _has_public_modifier(node: Node) -> bool:
    for child in node.children:
        if child.type == "modifiers":
            return any(grandchild.type == "public" for grandchild in child.children)
    return False


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
        if member.type in _METHOD_LIKE_SYMBOL_TYPES:
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


def _flatten_scoped_identifier(node: Node) -> str | None:
    """Flatten a `scoped_identifier` (or plain `identifier`) chain into a
    dotted string (`"java.util.List"`) — `None` for any other node shape."""
    if node.type == "identifier":
        return _decode(node)
    if node.type == "scoped_identifier":
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        if scope is not None and name is not None:
            base = _flatten_scoped_identifier(scope)
            return f"{base}.{_decode(name)}" if base is not None else None
    return None


def extract_relationships_java(source: str) -> list[Relationship]:
    """Return every deterministic import/inherits/implements_interface/
    overrides/calls relationship Java's grammar makes explicit (QB-067) —
    file-local and unresolved, see `relationship_model.py`'s module
    docstring.

    - **imports**: every `import_declaration` (including `import
      static ...`, treated identically — its final segment is still the
      name it binds locally, e.g. `Math.max` bound as `max`). `target` is
      the full dotted path; `qualifier` is its last segment, Java's own
      rule for what name an import makes available unqualified. A
      wildcard import (`import java.util.*;`/`import static
      java.lang.Math.*;`) is skipped entirely — same "ambiguous binding"
      exclusion as Python's `from x import *`.
    - **inherits**: a class's `extends` clause (`class Foo extends Base`
      — Java classes have at most one).
    - **implements_interface**: a class's `implements` clause (`implements
      A, B`) — syntactically distinct from `extends` in this grammar, so
      no ambiguity the way C#'s single colon-list has (see `csharp.py`).
    - **implements_trait**: never emitted — Java has no trait construct.
    - **overrides**: a method carrying an `@Override` annotation
      (`marker_annotation`/`annotation` named `Override`) — `qualifier`
      is the enclosing class's own raw `extends` target name (the
      superclass the override is claimed against); a method with no
      `@Override` annotation is not reported, even if it happens to
      share a name with a superclass method — no guessing beyond the
      explicit annotation.
    - **calls**: only from within a method/constructor already reported
      as a `Symbol` by `extract_symbols_java()` — `source` is that
      `Symbol`'s own name. A bare call (`helper()`), an explicit
      `this.other()`/`super.method()` call, and a qualified call
      (`Utils.staticCall()`) are each recorded (`qualifier=None`/`"this"`/
      `"super"`/`<name>` respectively) — a deeper chain is skipped.

    Returns an empty list (with the same actionable warning
    `analyze_java()` emits) if the optional dependency is missing.
    Otherwise may raise on a genuine, unrecoverable parser failure — same
    fail-open contract as `analyze_java()`."""
    try:
        import tree_sitter
        import tree_sitter_java
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-java is not installed; "
            "install quor[java] to enable Java relationship extraction "
            "(falling back to no relationships for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_java.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    relationships: list[Relationship] = []
    for child in tree.root_node.children:
        if child.type == "import_declaration":
            _add_import_relationship(child, relationships)
        elif child.type in _CLASS_LIKE_BODY_TYPES:
            _add_type_relationships(child, relationships)
    return relationships


def _add_import_relationship(node: Node, relationships: list[Relationship]) -> None:
    if any(c.type == "asterisk" for c in node.children):
        return
    scoped = next((c for c in node.children if c.type in ("scoped_identifier", "identifier")), None)
    if scoped is None:
        return
    target = _flatten_scoped_identifier(scoped)
    if target is None:
        return
    qualifier = target.rsplit(".", 1)[-1]
    # `origin=qualifier` (not None) signals a direct symbol binding — a Java
    # import always names one specific class/member, never a whole package
    # the way Python's `import x`/Go's `import "pkg"` do — see `graph.py`'s
    # resolution notes for why this distinction matters.
    relationships.append(
        Relationship(
            kind="import",
            source="",
            target=target,
            line=node.start_point.row + 1,
            qualifier=qualifier,
            origin=qualifier,
        )
    )


def _add_type_relationships(node: Node, relationships: list[Relationship]) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    type_name = _decode(name_node)

    superclass_name: str | None = None
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        base_node = next((c for c in superclass.children if c.type == "type_identifier"), None)
        if base_node is not None:
            superclass_name = _decode(base_node)
            relationships.append(
                Relationship(
                    kind="inherits", source=type_name, target=superclass_name, line=base_node.start_point.row + 1
                )
            )

    interfaces = node.child_by_field_name("interfaces")
    if interfaces is not None:
        type_list = next((c for c in interfaces.children if c.type == "type_list"), None)
        if type_list is not None:
            for type_node in type_list.children:
                if type_node.type == "type_identifier":
                    relationships.append(
                        Relationship(
                            kind="implements_interface",
                            source=type_name,
                            target=_decode(type_node),
                            line=type_node.start_point.row + 1,
                        )
                    )

    body = node.child_by_field_name("body")
    if body is None:
        return
    for member in body.children:
        if member.type in _METHOD_LIKE_SYMBOL_TYPES:
            _add_method_relationships(member, superclass_name, relationships)


def _has_override_annotation(node: Node) -> bool:
    for child in node.children:
        if child.type != "modifiers":
            continue
        for grandchild in child.children:
            if grandchild.type in ("marker_annotation", "annotation"):
                name_node = grandchild.child_by_field_name("name")
                if name_node is not None and _decode(name_node) == "Override":
                    return True
    return False


def _add_method_relationships(
    node: Node, superclass_name: str | None, relationships: list[Relationship]
) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    method_name = _decode(name_node)
    _collect_calls(node, method_name, relationships)
    if superclass_name is not None and _has_override_annotation(node):
        relationships.append(
            Relationship(
                kind="overrides",
                source=method_name,
                target=method_name,
                line=node.start_point.row + 1,
                qualifier=superclass_name,
            )
        )


def collect_import_statements_java(root: Node) -> list[ImportStatement]:
    """Return every top-level `import_declaration` (including `import
    static ...`, treated identically to a plain import — same choice
    `_add_import_relationship()` above already makes), in source order
    (QB-096). Java has no bare, headingless import shape at all — every
    import always groups under its package (everything but the last dotted
    segment) as `module`, mirroring `extract_relationships_java()`'s own
    `target.rsplit(".", 1)` split, just kept as two separate fields
    (`module`, name) instead of one dotted `target` string.
    """
    statements: list[ImportStatement] = []
    for child in root.children:
        if child.type != "import_declaration":
            continue
        line = child.start_point.row + 1
        end_line = child.end_point.row + 1
        is_wildcard = any(c.type == "asterisk" for c in child.children)
        scoped = next((c for c in child.children if c.type in ("scoped_identifier", "identifier")), None)
        if scoped is None:
            continue
        target = _flatten_scoped_identifier(scoped)
        if target is None:
            continue
        if is_wildcard:
            statements.append(ImportStatement(line=line, end_line=end_line, module=target, is_wildcard=True))
            continue
        module, _, name = target.rpartition(".")
        statements.append(
            ImportStatement(line=line, end_line=end_line, module=module, names=(ImportedName(name=name),))
        )
    return statements


def collapse_imports_java(source: str) -> list[ImportBlockReplacement]:
    """Return the collapsed replacement for every token-cheaper run of
    consecutive top-level `import` declarations (QB-096) — see
    `import_collapse.py`'s module docstring for the shared run-detection,
    rendering, and cost-gate rules this delegates to. No stdlib/third-party
    classification for Java — every import already groups under its own
    package heading, so the bucket concept (Python-only, see
    `import_collapse.py`) never applies here.

    Returns an empty list (with the same actionable warning `analyze_java()`
    emits) if the optional dependency is missing. Otherwise may raise on a
    genuine, unrecoverable parser failure — same fail-open contract as
    `analyze_java()`.
    """
    try:
        import tree_sitter
        import tree_sitter_java
    except ImportError:
        warnings.warn(
            "[quor] tree-sitter/tree-sitter-java is not installed; "
            "install quor[java] to enable Java import collapsing "
            "(falling back to no compression for this file)",
            stacklevel=2,
        )
        return []

    language = tree_sitter.Language(tree_sitter_java.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source.encode("utf-8"))

    statements = collect_import_statements_java(tree.root_node)
    if not statements:
        return []
    return collapse_import_runs(statements, source.split("\n"), comment_prefix="//")


def _collect_calls(node: Node, source_name: str, relationships: list[Relationship]) -> None:
    for descendant in iter_descendants(node):
        if descendant.type != "method_invocation":
            continue
        name_node = descendant.child_by_field_name("name")
        if name_node is None:
            continue
        line = descendant.start_point.row + 1
        object_node = descendant.child_by_field_name("object")
        if object_node is None:
            relationships.append(
                Relationship(kind="calls", source=source_name, target=_decode(name_node), line=line)
            )
        elif object_node.type in ("this", "super"):
            relationships.append(
                Relationship(
                    kind="calls",
                    source=source_name,
                    target=_decode(name_node),
                    line=line,
                    qualifier=object_node.type,
                )
            )
        elif object_node.type == "identifier":
            relationships.append(
                Relationship(
                    kind="calls",
                    source=source_name,
                    target=_decode(name_node),
                    line=line,
                    qualifier=_decode(object_node),
                )
            )
