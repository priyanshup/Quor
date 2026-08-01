"""QB-099A/QB-099C: deterministic, declaration-level structural diff for
Python.

Compares two versions of one Python file at the *declaration* level
(class/function/method), not the line level, so a reorder/rename/formatting-
only/import-only change — today 100% `+`/`-`-prefixed in `git diff`, and
therefore fully protected and uncompressible by every existing stage (see
`docs/design/QB-099-structural-diff-compression-investigation.md` §1) — can
be reported in one sentence instead of a full delete-then-insert.

Every matching decision here is EXACT structural equality (a canonical,
position-stripped AST dump, optionally with one identifier blinded for
rename detection) or exact statement-list equality for extract/inline —
never a similarity score or threshold. This is the hard constraint the
investigation validated is achievable for reorder/move/rename/formatting-
only/import-only, and *not* achievable for extract/inline in the general
case — QB-099D (extract/inline detection) was rejected by design for
exactly that reason; the extract/inline detector here is kept only because
it still correctly fires on the narrow case it *can* prove (an exact,
unmodified copy-paste), never because it was expected to generalize.

Scope, deliberately narrower than the investigation's own prototype:
  - Matching is hierarchical (recurse into a class's own members only once
    the class itself doesn't match exactly), not the investigation
    prototype's flat two-level list-with-post-hoc-dedup — this is what
    "the flat-traversal reuse assumption was wrong" in that investigation's
    Section 3.1 asked for.
  - A minimum-declaration-size floor (`_MIN_LINES_FOR_CONTENT_MATCH`) guards
    against two unrelated, trivial one-line declarations (`__repr__` stubs,
    etc.) being reported as "moved"/"renamed" purely from coincidental exact
    equality — mirrors QB-096's `_MIN_ENTRIES_TO_COLLAPSE` guard against a
    technically-true-but-useless result.
  - Cross-container relocation ("moved" — a method promoted to module scope,
    or relocated to a different class) was deliberately deferred out of
    QB-099A's own initial scope (kept its own PR small and reviewable — see
    QB-099A's backlog entry) and is implemented here as QB-099C:
    `_reconcile_cross_container_moves()`, a small post-pass over
    `diff_declarations()`'s already-hierarchical output, not a change to
    the matcher itself. Same-container hierarchical matching always gets
    first claim on every declaration — this pass only ever looks at what's
    left over as `"removed"`/`"added"` after that, so it can never steal a
    legitimate in-place match away in favor of a coincidental cross-
    container one. `_match_flat()` itself still never compares across
    containers directly (true by construction, since `old_children`/
    `new_children` always share one parent) — QB-099C achieves the same
    observable result by reconciling leftovers afterward instead.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass, field
from typing import Literal

from quor.pipeline.ast_summarize.declaration_model import Declaration
from quor.pipeline.ast_summarize.python import collect_import_statements_python
from quor.pipeline.stages._utils import line_tokens

OpKind = Literal[
    "unchanged", "reordered", "moved", "renamed", "modified",
    "extracted", "inlined", "added", "removed",
]

# A declaration shorter than this (signature through last body line,
# inclusive) is never treated as a confident exact/rename match — only as a
# same-qualname "modified" candidate, where identity comes from the name,
# not coincidental content equality. 3 lines excludes one- and two-line
# stubs (`def __repr__(self): return self.name`, a bare `pass` body) while
# still covering the smallest *meaningful* real function.
_MIN_LINES_FOR_CONTENT_MATCH = 3


@dataclass(frozen=True)
class Op:
    kind: OpKind
    old: Declaration | None
    new: Declaration | None
    detail: str = ""


@dataclass(frozen=True)
class StructuralDiffResult:
    classification: Literal["formatting-only", "import-only", "structural", "unparseable"]
    ops: list[Op] = field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def _is_self_reference(node: ast.AST, field_name: str, field_value: object, target: str) -> bool:
    """True if `field_value` is the one field on `node` that spells out
    `target`'s own identifier — a `Name.id`, an `Attribute.attr` (covers
    `self.target(...)`-style method self-calls), or a nested
    `FunctionDef`/`AsyncFunctionDef`/`ClassDef.name` shadowing it."""
    if field_value != target:
        return False
    if isinstance(node, ast.Name):
        return field_name == "id"
    if isinstance(node, ast.Attribute):
        return field_name == "attr"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return field_name == "name"
    return False


def _canon(node: object, target: str | None) -> object:
    """Position-stripped structural dump of `node`, optionally with every
    identifier-bearing field equal to `target` replaced by a fixed
    placeholder — see module docstring on why this makes rename detection
    exact even for a recursive function's own self-calls."""
    if isinstance(node, ast.AST):
        parts: list[object] = [type(node).__name__]
        for fname, fval in ast.iter_fields(node):
            if target is not None and _is_self_reference(node, fname, fval, target):
                fval = "<SELF>"
            parts.append((fname, _canon(fval, target)))
        return tuple(parts)
    if isinstance(node, list):
        return tuple(_canon(v, target) for v in node)
    return node


def _full_dump(d: Declaration) -> object:
    return _canon(d.node, None)


def _blind_dump(d: Declaration) -> object:
    return _canon(d.node, d.name)


def _stmt_eq(a: ast.stmt, b: ast.stmt) -> bool:
    return _canon(a, None) == _canon(b, None)


# ---------------------------------------------------------------------------
# Extract/inline: exact contiguous-subsequence + single-call replacement
# ---------------------------------------------------------------------------


def _is_call_to(stmt: ast.stmt, name: str) -> bool:
    val: ast.expr | None = None
    if isinstance(stmt, (ast.Expr, ast.Return, ast.Assign, ast.AnnAssign)):
        val = stmt.value
    if not isinstance(val, ast.Call):
        return False
    func = val.func
    if isinstance(func, ast.Name):
        return func.id == name
    if isinstance(func, ast.Attribute):
        return func.attr == name
    return False


def _find_subsequence(haystack: list[ast.stmt], needle: list[ast.stmt]) -> tuple[int, int] | None:
    if not needle:
        return None
    n = len(needle)
    for i in range(0, len(haystack) - n + 1):
        if all(_stmt_eq(haystack[i + k], needle[k]) for k in range(n)):
            return (i, i + n)
    return None


def _try_extracted(old_body: list[ast.stmt], new_body: list[ast.stmt], helper: Declaration) -> bool:
    h_body = helper.node.body
    span = _find_subsequence(old_body, h_body)
    if span is None:
        return False
    i, j = span
    if len(new_body) != len(old_body) - (j - i) + 1:
        return False
    if not all(_stmt_eq(new_body[k], old_body[k]) for k in range(i)):
        return False
    if not _is_call_to(new_body[i], helper.name):
        return False
    tail_old, tail_new = old_body[j:], new_body[i + 1:]
    return len(tail_old) == len(tail_new) and all(
        _stmt_eq(a, b) for a, b in zip(tail_old, tail_new, strict=True)
    )


def _try_inlined(old_body: list[ast.stmt], new_body: list[ast.stmt], helper: Declaration) -> bool:
    call_idx = None
    for k, stmt in enumerate(old_body):
        if _is_call_to(stmt, helper.name):
            if call_idx is not None:
                return False  # more than one call site — ambiguous, skip
            call_idx = k
    if call_idx is None:
        return False
    expected = old_body[:call_idx] + helper.node.body + old_body[call_idx + 1:]
    return len(expected) == len(new_body) and all(
        _stmt_eq(a, b) for a, b in zip(expected, new_body, strict=True)
    )


# ---------------------------------------------------------------------------
# Direct-children extraction (one level only — no recursion into a found
# class's own body; the matcher recurses explicitly, only when needed)
# ---------------------------------------------------------------------------


def _direct_children(stmts: list[ast.stmt], container: Literal["module", "class"], parent: str | None) -> list[Declaration]:
    decls: list[Declaration] = []
    counters: dict[str, int] = {}
    func_kind: Literal["function", "method"] = "function" if container == "module" else "method"

    def visit(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                idx = counters.get("class", 0)
                counters["class"] = idx + 1
                end = stmt.end_lineno if stmt.end_lineno is not None else stmt.lineno
                decls.append(Declaration("class", stmt.name, parent, stmt, idx, stmt.lineno, end))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                idx = counters.get(func_kind, 0)
                counters[func_kind] = idx + 1
                end = stmt.end_lineno if stmt.end_lineno is not None else stmt.lineno
                decls.append(Declaration(func_kind, stmt.name, parent, stmt, idx, stmt.lineno, end))
            elif container == "module" and isinstance(stmt, ast.If):
                visit(stmt.body)
                visit(stmt.orelse)
            elif container == "module" and isinstance(stmt, ast.Try):
                visit(stmt.body)
                for handler in stmt.handlers:
                    visit(handler.body)
                visit(stmt.orelse)
                visit(stmt.finalbody)
            elif container == "module" and isinstance(stmt, (ast.With, ast.AsyncWith)):
                visit(stmt.body)

    visit(stmts)
    return decls


# ---------------------------------------------------------------------------
# Matching — one container level at a time; the caller recurses
# ---------------------------------------------------------------------------


def _match_flat(old_children: list[Declaration], new_children: list[Declaration]) -> list[Op]:
    """Match declarations that share one container (one class's direct
    members, or the module's own top-level declarations) — never across
    containers. Cross-container relocation is QB-099C's own scope: it
    naturally cannot be expressed here, since `old_children`/`new_children`
    are, by construction, always from the same parent."""
    ops: list[Op] = []
    remaining_old = list(old_children)
    remaining_new = list(new_children)

    # Step 1 — exact structural match (content byte-for-byte AST-identical),
    # gated by the minimum-size floor.
    buckets: dict[object, list[Declaration]] = {}
    for d in remaining_old:
        if d.line_count >= _MIN_LINES_FOR_CONTENT_MATCH:
            buckets.setdefault(_full_dump(d), []).append(d)
    matched_old, matched_new = set(), set()
    for d in remaining_new:
        if d.line_count < _MIN_LINES_FOR_CONTENT_MATCH:
            continue
        bucket = buckets.get(_full_dump(d))
        if not bucket:
            continue
        for cand in bucket:
            if id(cand) not in matched_old:
                matched_old.add(id(cand))
                matched_new.add(id(d))
                kind: OpKind = "unchanged" if cand.index == d.index else "reordered"
                ops.append(Op(kind, cand, d))
                break
    remaining_old = [d for d in remaining_old if id(d) not in matched_old]
    remaining_new = [d for d in remaining_new if id(d) not in matched_new]

    # Step 2 — rename match (structurally identical modulo declared name),
    # same size floor.
    used_new: set[int] = set()
    still_old = []
    for od in remaining_old:
        found = None
        if od.line_count >= _MIN_LINES_FOR_CONTENT_MATCH:
            for nd in remaining_new:
                if id(nd) in used_new or nd.kind != od.kind or nd.name == od.name:
                    continue
                if nd.line_count < _MIN_LINES_FOR_CONTENT_MATCH:
                    continue
                if _blind_dump(od) == _blind_dump(nd):
                    found = nd
                    break
        if found is not None:
            used_new.add(id(found))
            ops.append(Op("renamed", od, found, detail=f"{od.qualname} -> {found.qualname}"))
        else:
            still_old.append(od)
    remaining_old = still_old
    remaining_new = [d for d in remaining_new if id(d) not in used_new]

    # Step 3 — same-qualname match (identity by name, size floor does not
    # apply — the floor exists to stop *coincidental* exact-content matches
    # between two different declarations from being mistaken for a real
    # correspondence; a same-name match already establishes identity by
    # construction, so there's nothing left to guard against). Content is
    # still compared here, ungated: a below-floor declaration whose content
    # is genuinely unchanged (e.g. a 2-line `def b(): return 1`) must be
    # reported as "unchanged"/"reordered", not "modified" with an empty
    # diff — Steps 1/2 never got a chance to say so because the floor kept
    # it out of their hash buckets.
    new_by_name: dict[str, Declaration] = {d.name: d for d in remaining_new}
    still_old = []
    matched_name: set[int] = set()
    for od in remaining_old:
        by_name = new_by_name.get(od.name)
        if by_name is not None and by_name.kind == od.kind and id(by_name) not in matched_name:
            matched_name.add(id(by_name))
            if _full_dump(od) == _full_dump(by_name):
                same_name_kind: OpKind = "unchanged" if od.index == by_name.index else "reordered"
                ops.append(Op(same_name_kind, od, by_name))
            else:
                ops.append(Op("modified", od, by_name))
        else:
            still_old.append(od)
    remaining_old = still_old
    remaining_new = [d for d in remaining_new if id(d) not in matched_name]

    # Step 4 — extract/inline (exact contiguous-subsequence + call check),
    # function/method kind only.
    modified_ops = [o for o in ops if o.kind == "modified" and o.new is not None and o.new.kind in ("function", "method")]
    consumed_new: set[int] = set()
    for op in modified_ops:
        assert op.old is not None and op.new is not None
        for h in remaining_new:
            if id(h) in consumed_new or h.kind not in ("function", "method"):
                continue
            if _try_extracted(op.old.node.body, op.new.node.body, h):
                ops[ops.index(op)] = Op("extracted", op.old, op.new, detail=f"{h.qualname} extracted from {op.new.qualname}")
                consumed_new.add(id(h))
                break
    remaining_new = [d for d in remaining_new if id(d) not in consumed_new]

    consumed_old: set[int] = set()
    for i, op in enumerate(ops):
        if op.kind != "modified" or op.new is None or op.new.kind not in ("function", "method"):
            continue
        assert op.old is not None
        for h in remaining_old:
            if id(h) in consumed_old or h.kind not in ("function", "method"):
                continue
            if _try_inlined(op.old.node.body, op.new.node.body, h):
                ops[i] = Op("inlined", op.old, op.new, detail=f"{h.qualname} inlined into {op.new.qualname}")
                consumed_old.add(id(h))
                break
    remaining_old = [d for d in remaining_old if id(d) not in consumed_old]

    # Step 5 — leftovers.
    for od in remaining_old:
        ops.append(Op("removed", od, None))
    for nd in remaining_new:
        ops.append(Op("added", None, nd))

    return ops


def diff_declarations(old_stmts: list[ast.stmt], new_stmts: list[ast.stmt], parent: str | None = None) -> list[Op]:
    """Match one container's direct children, then recurse into any class
    that matched by name but whose content differs ("modified") — true
    hierarchical matching, so a changed method inside an otherwise-unchanged
    class is reported once (at the method), not twice (once for the class's
    own text diff, once for the method)."""
    container: Literal["module", "class"] = "class" if parent is not None else "module"
    old_children = _direct_children(old_stmts, container, parent)
    new_children = _direct_children(new_stmts, container, parent)
    ops = _match_flat(old_children, new_children)

    result: list[Op] = []
    for op in ops:
        result.append(op)
        if op.kind == "modified" and op.new is not None and op.new.kind == "class":
            assert op.old is not None
            result.extend(diff_declarations(op.old.node.body, op.new.node.body, parent=op.new.qualname))
    return result


def _kind_compatible(a: str, b: str) -> bool:
    """True if `a`/`b` are the same `Declaration.kind`, or the one pair that
    a cross-container move can legitimately change: `"function"` <->
    `"method"` — `Declaration.kind` labels a plain `ast.FunctionDef` as
    `"function"` at module scope and `"method"` one level inside a class
    (see `_direct_children()`), so a method promoted to module scope (or a
    module-level function demoted into a class) changes this label even
    though the underlying AST node type never does. A `"class"` never
    matches either — classes and callables are never the same declaration
    under any relocation."""
    return a == b or {a, b} == {"function", "method"}


def _reconcile_cross_container_moves(ops: list[Op]) -> list[Op]:
    """QB-099C: pair a leftover `"removed"` declaration with a leftover
    `"added"` declaration when their content is exactly identical (same
    `_MIN_LINES_FOR_CONTENT_MATCH` floor as Steps 1/2 — a coincidental match
    between two trivial, unrelated stubs must never be reported as a move)
    and their kinds are compatible (see `_kind_compatible()`) — reported as
    `"moved"` instead of two independent entries. A pure post-pass over
    `diff_declarations()`'s already-flat, already-hierarchical output:
    same-container matching always runs first and claims every declaration
    it can, so anything that reaches this function as `"removed"`/`"added"`
    genuinely couldn't be placed within its own original container — see
    module docstring."""
    removed = [op for op in ops if op.kind == "removed"]
    added = [op for op in ops if op.kind == "added"]
    if not removed or not added:
        return ops

    buckets: dict[object, list[Op]] = {}
    for op in removed:
        assert op.old is not None
        if op.old.line_count >= _MIN_LINES_FOR_CONTENT_MATCH:
            buckets.setdefault(_full_dump(op.old), []).append(op)

    moved_ops: list[Op] = []
    consumed_removed: set[int] = set()
    consumed_added: set[int] = set()
    for add_op in added:
        assert add_op.new is not None
        if add_op.new.line_count < _MIN_LINES_FOR_CONTENT_MATCH:
            continue
        for rem_op in buckets.get(_full_dump(add_op.new), []):
            assert rem_op.old is not None
            if id(rem_op) in consumed_removed or not _kind_compatible(rem_op.old.kind, add_op.new.kind):
                continue
            consumed_removed.add(id(rem_op))
            consumed_added.add(id(add_op))
            moved_ops.append(Op("moved", rem_op.old, add_op.new))
            break

    result: list[Op] = []
    for op in ops:
        if op.kind == "removed" and id(op) in consumed_removed:
            continue
        if op.kind == "added" and id(op) in consumed_added:
            continue
        result.append(op)
    result.extend(moved_ops)
    return result


# ---------------------------------------------------------------------------
# File-level classification + rendering
# ---------------------------------------------------------------------------


def diff_python_files(old_src: str, new_src: str) -> StructuralDiffResult:
    try:
        old_tree = ast.parse(old_src)
        new_tree = ast.parse(new_src)
    except (SyntaxError, ValueError):
        return StructuralDiffResult("unparseable")

    ops = diff_declarations(old_tree.body, new_tree.body, parent=None)
    ops = _reconcile_cross_container_moves(ops)  # QB-099C

    # "reordered"/"moved"/"renamed"/"extracted"/"inlined"/"modified"/"added"/
    # "removed" are all real, reportable structural facts; only "unchanged"
    # (content AND position both identical) means there is truly nothing to
    # say about a declaration — see the investigation's own §3.1 on why
    # conflating "AST-identical" with "nothing to report" is a real bug, not
    # a simplification.
    non_trivial = [o for o in ops if o.kind != "unchanged"]
    if not non_trivial:
        old_imports = collect_import_statements_python(old_tree)
        new_imports = collect_import_statements_python(new_tree)
        # Deliberately NOT order-normalized (no `sorted()`): a pure import
        # *reorder* is itself a real, reportable import-only fact — sorting
        # both sides before comparing would make it indistinguishable from
        # "nothing changed" and silently fall through to the less specific
        # "formatting-only" label instead.
        old_lines, new_lines = old_src.split("\n"), new_src.split("\n")
        old_import_text = [tuple(old_lines[i.line - 1:i.end_line]) for i in old_imports]
        new_import_text = [tuple(new_lines[i.line - 1:i.end_line]) for i in new_imports]
        if old_import_text != new_import_text:
            return StructuralDiffResult(
                "import-only", ops, note=f"{len(old_imports)} -> {len(new_imports)} import statements"
            )
        return StructuralDiffResult("formatting-only", ops)

    return StructuralDiffResult("structural", ops)


def _lines_of(node: object, src_lines: list[str]) -> list[str]:
    if not isinstance(node, ast.AST) or not hasattr(node, "lineno"):
        return []
    start = node.lineno - 1
    end = node.end_lineno if node.end_lineno is not None else node.lineno  # type: ignore[attr-defined]
    return src_lines[start:end]


def _scoped_diff(old: Declaration, new: Declaration, old_lines: list[str], new_lines: list[str]) -> str:
    old_text = _lines_of(old.node, old_lines)
    new_text = _lines_of(new.node, new_lines)
    diff = difflib.unified_diff(old_text, new_text, lineterm="", n=1)
    return "\n".join(f"    {line}" for line in diff)


_ORDER: dict[str, int] = {
    "removed": 0, "extracted": 1, "inlined": 1, "modified": 1,
    "renamed": 2, "moved": 3, "reordered": 4,
}


def render(result: StructuralDiffResult, old_src: str, new_src: str) -> str:
    if result.classification == "unparseable":
        return ""  # caller must fall back to the original diff text unchanged
    if result.classification == "formatting-only":
        return "formatting-only change (no AST difference in any declaration)"
    if result.classification == "import-only":
        return f"import-only change ({result.note})"

    old_lines, new_lines = old_src.split("\n"), new_src.split("\n")
    lines: list[str] = []
    for op in sorted(result.ops, key=lambda o: _ORDER.get(o.kind, 9)):
        if op.kind == "unchanged":
            continue
        if op.kind == "reordered":
            assert op.new is not None
            lines.append(f"reordered: {op.new.qualname} (unchanged content, now position {op.new.index + 1})")
        elif op.kind == "moved":
            assert op.old is not None and op.new is not None
            lines.append(f"moved: {op.old.qualname} -> {op.new.qualname} (unchanged content)")
        elif op.kind == "renamed":
            lines.append(f"renamed: {op.detail} (body unchanged)")
        elif op.kind == "extracted":
            lines.append(f"extracted: {op.detail}")
        elif op.kind == "inlined":
            lines.append(f"inlined: {op.detail}")
        elif op.kind == "modified":
            assert op.old is not None and op.new is not None
            if op.new.kind == "class":
                lines.append(f"modified: class {op.new.qualname} (members changed, see below)")
            else:
                lines.append(f"modified: {op.new.qualname}\n{_scoped_diff(op.old, op.new, old_lines, new_lines)}")
        elif op.kind == "added":
            assert op.new is not None
            lines.append(f"added: {op.new.qualname}")
        elif op.kind == "removed":
            assert op.old is not None
            lines.append(f"removed: {op.old.qualname}")
    return "\n".join(lines) if lines else "no structural changes"


def render_tokens(text: str) -> int:
    return sum(line_tokens(line) for line in text.split("\n"))
