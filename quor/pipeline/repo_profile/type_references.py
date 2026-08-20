"""R-08 (Graph-Distance AST Tiering) — cross-file symbol coherence:
extract the bare custom-type names a Python function's parameter/return
type annotations reference (e.g. `Widget` out of `Optional[Widget]`,
`list[Widget]`, `Widget | None`, or a `"Widget"` string forward-reference),
so a 1-hop file's kept signature can be checked against a 2-hop file's
declared types.

Python-only, deliberately: no `RelationshipKind` in `relationship_model.py`
captures a type-annotation reference edge (only import/inherits/export/
calls — see that module's own docstring), and no existing helper anywhere
in this codebase parses an annotation expression into referenced names —
`javascript.py`/`typescript.py`/`rust.py`'s `_type_name()` variants only
handle class-heritage (`extends`/`implements`) nodes, not general
parameter/return annotations. This is genuinely new, and scoped to Python
because that's the one language whose declaration data
(`declaration_model.py`) carries a live `ast` node to walk — extending to
the tree-sitter languages is real, bounded, future work (mirrors
`declaration_model.py`'s own "Python-only for now" scope note).
"""

from __future__ import annotations

import ast

_BUILTIN_TYPE_NAMES = frozenset(
    {
        "int", "str", "float", "bool", "bytes", "bytearray", "complex",
        "list", "dict", "set", "tuple", "frozenset", "memoryview", "range",
        "slice", "object", "type", "None",
        "Any", "Optional", "Union", "Callable", "Iterable", "Iterator",
        "Sequence", "Mapping", "MutableMapping", "Generator", "Coroutine",
        "Awaitable", "ClassVar", "Final", "Literal", "Protocol", "TypeVar",
        "Generic", "NamedTuple", "TypedDict", "Self", "NoReturn", "Never",
    }
)
"""`typing` module and builtin names that are never themselves a custom
type another file might define — excluded so the coherence pass never
goes hunting for a class literally named "Optional" or "list"."""


def _names_in_expr(node: ast.expr) -> set[str]:
    """Walk one annotation expression, collecting every bare `ast.Name`/
    `ast.Attribute` leaf's identifier. Covers the shapes a real-world type
    annotation actually takes: a bare name (`Widget`), a subscripted
    generic (`Optional[Widget]`, `list[Widget]`), a PEP 604 union
    (`Widget | None`), a dotted/qualified name (`module.Widget` — only the
    final `Widget` is meaningful as a bare cross-file symbol name), and a
    string forward-reference (`"Widget"`, re-parsed as its own
    expression)."""
    names: set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, ast.Attribute):
        names.add(node.attr)
    elif isinstance(node, ast.Subscript):
        names |= _names_in_expr(node.value)
        names |= _names_in_expr(node.slice)
    elif isinstance(node, ast.Tuple | ast.List):
        for elt in node.elts:
            names |= _names_in_expr(elt)
    elif isinstance(node, ast.BinOp):
        names |= _names_in_expr(node.left)
        names |= _names_in_expr(node.right)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            parsed = ast.parse(node.value, mode="eval")
        except (SyntaxError, ValueError):
            return names
        names |= _names_in_expr(parsed.body)
    return names


def referenced_type_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every custom (non-builtin, non-typing) type name referenced in
    `node`'s parameter and return annotations.

    Fail-open by construction rather than by `try`/`except`: every branch
    in `_names_in_expr()` only ever adds to a `set`, so a real-world
    annotation shape this doesn't specifically recognize (an unusual
    `ast.expr` subtype) simply contributes no names rather than raising —
    there is no failure mode to catch here.
    """
    names: set[str] = set()
    args = node.args
    all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg is not None:
        all_args.append(args.vararg)
    if args.kwarg is not None:
        all_args.append(args.kwarg)
    for arg in all_args:
        if arg.annotation is not None:
            names |= _names_in_expr(arg.annotation)
    if node.returns is not None:
        names |= _names_in_expr(node.returns)
    return names - _BUILTIN_TYPE_NAMES
