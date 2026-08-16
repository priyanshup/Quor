"""QB-099 prototype: deterministic, AST-aware structural diff for Python.

Not part of the Quor package. Answers architectural questions only — see
docs/design/QB-099-structural-diff-compression-investigation.md for the
write-up this supports. Reuses quor.pipeline.ast_summarize.python for
parsing/extraction wherever that module's existing return shape already
fits (imports); declarations need a richer shape (the actual AST node, not
just a Symbol's name/line) than Symbol carries, so extraction is
reimplemented here at prototype scope, mirroring python.py's own
_visit_module_body/_visit_class_body traversal exactly. A production
implementation would add this as a third sibling to those two functions
(returning (Symbol, ast.AST) pairs) rather than a second copy — see the
write-up's "reuse audit" section.

Every matching decision below is EXACT structural equality (a canonical,
position-stripped AST dump, optionally with one identifier blinded) or
exact statement-list equality — never a similarity score or threshold, per
QB-099's own "no fuzzy similarity" constraint.
"""

from __future__ import annotations

import ast
import difflib
import math
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["function", "method", "class"]
OpKind = Literal[
    "unchanged", "reordered", "moved", "renamed", "modified",
    "extracted", "inlined", "added", "removed",
]


@dataclass
class Decl:
    kind: Kind
    name: str
    parent: str | None  # class name for methods/nested classes, else None
    node: ast.AST
    index: int  # source-order rank among siblings of the same kind+parent
    lines: list[str]  # this declaration's own source lines (for scoped diffs)

    @property
    def qualname(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass
class Op:
    kind: OpKind
    old: Decl | None
    new: Decl | None
    detail: str = ""


@dataclass
class FileSummary:
    classification: Literal["formatting-only", "import-only", "structural"]
    ops: list[Op] = field(default_factory=list)
    import_note: str = ""


# --------------------------------------------------------------------------
# Extraction (mirrors python.py's _visit_module_body/_visit_class_body)
# --------------------------------------------------------------------------

def extract_decls(tree: ast.Module, src_lines: list[str]) -> list[Decl]:
    decls: list[Decl] = []
    _visit_module(tree.body, None, decls, src_lines)
    return decls


def _slice(node: ast.AST, src_lines: list[str]) -> list[str]:
    start = node.lineno - 1
    end = node.end_lineno or node.lineno
    return src_lines[start:end]


def _visit_module(stmts, parent, decls, src_lines, _counters=None):
    counters = _counters if _counters is not None else {}
    for stmt in stmts:
        if isinstance(stmt, ast.ClassDef):
            idx = counters.get(("class", parent), 0)
            counters[("class", parent)] = idx + 1
            decls.append(Decl("class", stmt.name, parent, stmt, idx, _slice(stmt, src_lines)))
            _visit_class(stmt.body, stmt.name, decls, src_lines)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            idx = counters.get(("function", parent), 0)
            counters[("function", parent)] = idx + 1
            decls.append(Decl("function", stmt.name, parent, stmt, idx, _slice(stmt, src_lines)))
        elif isinstance(stmt, ast.If):
            _visit_module(stmt.body, parent, decls, src_lines, counters)
            _visit_module(stmt.orelse, parent, decls, src_lines, counters)
        elif isinstance(stmt, ast.Try):
            _visit_module(stmt.body, parent, decls, src_lines, counters)
            for h in stmt.handlers:
                _visit_module(h.body, parent, decls, src_lines, counters)
            _visit_module(stmt.orelse, parent, decls, src_lines, counters)
            _visit_module(stmt.finalbody, parent, decls, src_lines, counters)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            _visit_module(stmt.body, parent, decls, src_lines, counters)


def _visit_class(stmts, class_name, decls, src_lines):
    idx = 0
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decls.append(Decl("method", stmt.name, class_name, stmt, idx, _slice(stmt, src_lines)))
            idx += 1
        elif isinstance(stmt, ast.ClassDef):
            decls.append(Decl("class", stmt.name, class_name, stmt, idx, _slice(stmt, src_lines)))
            idx += 1
            _visit_class(stmt.body, stmt.name, decls, src_lines)


# --------------------------------------------------------------------------
# Canonicalization (position-stripped, optionally one identifier blinded)
# --------------------------------------------------------------------------

def _canon(node, target: str | None):
    if isinstance(node, ast.AST):
        parts = [type(node).__name__]
        for fname, fval in ast.iter_fields(node):
            if target is not None and (
                (isinstance(node, ast.Name) and fname == "id" and fval == target)
                or (isinstance(node, ast.Attribute) and fname == "attr" and fval == target)
                or (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and fname == "name"
                    and fval == target
                )
            ):
                fval = "<SELF>"
            parts.append((fname, _canon(fval, target)))
        return tuple(parts)
    if isinstance(node, list):
        return tuple(_canon(v, target) for v in node)
    return node


def _full_dump(d: Decl):
    return _canon(d.node, None)


def _blind_dump(d: Decl):
    return _canon(d.node, d.name)


def _stmt_eq(a: ast.stmt, b: ast.stmt) -> bool:
    return _canon(a, None) == _canon(b, None)


# --------------------------------------------------------------------------
# Extract/inline detection: exact contiguous-subsequence + call-replacement
# --------------------------------------------------------------------------

def _is_call_to(stmt: ast.stmt, name: str) -> bool:
    val = None
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


def _try_extracted(old_body: list[ast.stmt], new_body: list[ast.stmt], helper: Decl) -> bool:
    """True if `helper`'s entire body is an exact contiguous slice of
    `old_body`, and `new_body` equals `old_body` with that slice replaced by
    one call to `helper`."""
    h_body = helper.node.body
    span = _find_subsequence(old_body, h_body)
    if span is None:
        return False
    i, j = span
    expected_len = len(old_body) - (j - i) + 1
    if len(new_body) != expected_len:
        return False
    if not all(_stmt_eq(new_body[k], old_body[k]) for k in range(i)):
        return False
    if not _is_call_to(new_body[i], helper.name):
        return False
    tail_old = old_body[j:]
    tail_new = new_body[i + 1:]
    # strict=False: an intentional truncate-then-compare — the trailing
    # len() check (not zip itself) is what actually enforces equal length,
    # so this must return False on mismatch, never raise.
    return all(_stmt_eq(a, b) for a, b in zip(tail_old, tail_new, strict=False)) and len(tail_old) == len(tail_new)


def _try_inlined(old_body: list[ast.stmt], new_body: list[ast.stmt], helper: Decl) -> bool:
    """True if `old_body` contains exactly one call to `helper` and
    `new_body` equals `old_body` with that one call statement replaced by
    `helper`'s full body, verbatim."""
    call_idx = None
    for k, stmt in enumerate(old_body):
        if _is_call_to(stmt, helper.name):
            if call_idx is not None:
                return False  # more than one call site — ambiguous, skip
            call_idx = k
    if call_idx is None:
        return False
    h_body = helper.node.body
    expected = old_body[:call_idx] + h_body + old_body[call_idx + 1:]
    if len(expected) != len(new_body):
        return False
    return all(_stmt_eq(a, b) for a, b in zip(expected, new_body, strict=True))


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def diff_decls(old_decls: list[Decl], new_decls: list[Decl]) -> list[Op]:
    ops: list[Op] = []
    remaining_old = list(old_decls)
    remaining_new = list(new_decls)

    # Step 1 — exact structural match (content byte-for-byte AST-identical).
    buckets: dict[object, list[Decl]] = {}
    for d in remaining_old:
        buckets.setdefault(_full_dump(d), []).append(d)
    matched_old, matched_new = set(), set()
    for d in remaining_new:
        bucket = buckets.get(_full_dump(d))
        if not bucket:
            continue
        for cand in bucket:
            if id(cand) not in matched_old:
                matched_old.add(id(cand))
                matched_new.add(id(d))
                if cand.parent != d.parent:
                    kind: OpKind = "moved"
                elif cand.index != d.index:
                    kind = "reordered"
                else:
                    kind = "unchanged"
                ops.append(Op(kind, cand, d))
                break
    remaining_old = [d for d in remaining_old if id(d) not in matched_old]
    remaining_new = [d for d in remaining_new if id(d) not in matched_new]

    # Step 2 — rename match (structurally identical modulo declared name).
    used_new = set()
    still_old = []
    for od in remaining_old:
        found = None
        for nd in remaining_new:
            if id(nd) in used_new or nd.kind != od.kind or nd.name == od.name:
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

    # Step 3 — same-qualname match => modified.
    new_by_q: dict[str, Decl] = {d.qualname: d for d in remaining_new}
    still_old = []
    matched_q = set()
    for od in remaining_old:
        nd = new_by_q.get(od.qualname)
        if nd is not None and nd.kind == od.kind and id(nd) not in matched_q:
            matched_q.add(id(nd))
            ops.append(Op("modified", od, nd))
        else:
            still_old.append(od)
    remaining_old = still_old
    remaining_new = [d for d in remaining_new if id(d) not in matched_q]

    # Step 4 — extract/inline (exact contiguous-subsequence + call check).
    modified_ops = [o for o in ops if o.kind == "modified"]
    consumed_helpers: set[int] = set()
    for op in modified_ops:
        for h in remaining_new:
            if id(h) in consumed_helpers or h.kind not in ("function", "method"):
                continue
            if _try_extracted(op.old.node.body, op.new.node.body, h):
                op.kind = "extracted"
                op.detail = f"{h.qualname} extracted from {op.new.qualname}"
                consumed_helpers.add(id(h))
                break
    remaining_new = [d for d in remaining_new if id(d) not in consumed_helpers]

    consumed_helpers_old: set[int] = set()
    for op in modified_ops:
        if op.kind == "extracted":
            continue
        for h in remaining_old:
            if id(h) in consumed_helpers_old or h.kind not in ("function", "method"):
                continue
            if _try_inlined(op.old.node.body, op.new.node.body, h):
                op.kind = "inlined"
                op.detail = f"{h.qualname} inlined into {op.new.qualname}"
                consumed_helpers_old.add(id(h))
                break
    remaining_old = [d for d in remaining_old if id(d) not in consumed_helpers_old]

    # Step 5 — leftovers.
    for od in remaining_old:
        ops.append(Op("removed", od, None))
    for nd in remaining_new:
        ops.append(Op("added", None, nd))

    return ops


# --------------------------------------------------------------------------
# File-level classification + rendering
# --------------------------------------------------------------------------

def classify_file(old_src: str, new_src: str) -> FileSummary:
    old_tree = ast.parse(old_src)
    new_tree = ast.parse(new_src)
    old_lines = old_src.split("\n")
    new_lines = new_src.split("\n")
    old_decls = extract_decls(old_tree, old_lines)
    new_decls = extract_decls(new_tree, new_lines)
    ops = diff_decls(old_decls, new_decls)

    # "reordered"/"moved" are real, reportable structural facts (a developer
    # asked to recognize "reordered declarations"/"method move"/"class move"
    # explicitly) — only "unchanged" (content AND position both identical)
    # means there is truly nothing to say about a declaration. Collapsing
    # "reordered" into the same bucket as "unchanged" would silently drop
    # exactly the information this investigation is about preserving.
    non_trivial = [o for o in ops if o.kind != "unchanged"]
    if not non_trivial:
        from quor.pipeline.ast_summarize.python import collect_import_statements_python

        old_imports = collect_import_statements_python(old_tree)
        new_imports = collect_import_statements_python(new_tree)
        old_import_text = [tuple(old_lines[i.line - 1:i.end_line]) for i in old_imports]
        new_import_text = [tuple(new_lines[i.line - 1:i.end_line]) for i in new_imports]
        if sorted(old_import_text) != sorted(new_import_text) or len(old_imports) != len(new_imports):
            return FileSummary("import-only", ops, import_note=f"{len(old_imports)} -> {len(new_imports)} import statements")
        if old_src != new_src:
            return FileSummary("formatting-only", ops)
        return FileSummary("formatting-only", ops)  # byte-identical

    return FileSummary("structural", ops)


def _scoped_diff(old: Decl, new: Decl) -> str:
    diff = difflib.unified_diff(old.lines, new.lines, lineterm="", n=1)
    return "\n".join(f"    {line}" for line in diff)


_ORDER = {"removed": 0, "extracted": 1, "inlined": 1, "modified": 1, "renamed": 2, "moved": 3, "reordered": 4, "added": 5}


def render(summary: FileSummary) -> str:
    if summary.classification == "formatting-only":
        return "formatting-only change (no AST difference in any declaration)"
    if summary.classification == "import-only":
        return f"import-only change ({summary.import_note})"

    lines = []
    for op in sorted(summary.ops, key=lambda o: _ORDER.get(o.kind, 9)):
        if op.kind == "unchanged":
            continue
        elif op.kind == "reordered":
            lines.append(f"reordered: {op.new.qualname} (unchanged content, now position {op.new.index + 1})")
        elif op.kind == "moved":
            lines.append(f"moved: {op.old.qualname} -> {op.new.qualname} (unchanged content)")
        elif op.kind == "renamed":
            lines.append(f"renamed: {op.detail} (body unchanged)")
        elif op.kind == "extracted":
            lines.append(f"extracted: {op.detail}")
        elif op.kind == "inlined":
            lines.append(f"inlined: {op.detail}")
        elif op.kind == "modified":
            if op.new.kind == "class":
                # A class's own text diff is redundant with its members' own
                # ops (already flat-listed and diffed independently below) —
                # rendering both double-counts the same change. Real
                # hierarchical matching (recurse into children only when the
                # parent doesn't already match exactly) avoids this by
                # construction; this flat-list prototype approximates it by
                # suppressing the parent's own diff whenever member-level
                # ops exist for it.
                has_member_ops = any(
                    o is not op and o.kind != "unchanged"
                    and ((o.new and o.new.parent == op.new.name) or (o.old and o.old.parent == op.new.name))
                    for o in summary.ops
                )
                if has_member_ops:
                    lines.append(f"modified: class {op.new.qualname} (members changed, see below)")
                    continue
            lines.append(f"modified: {op.new.qualname}\n{_scoped_diff(op.old, op.new)}")
        elif op.kind == "added":
            lines.append(f"added: {op.new.qualname}")
        elif op.kind == "removed":
            lines.append(f"removed: {op.old.qualname}")
    return "\n".join(lines) if lines else "no structural changes"


def line_tokens(line: str) -> int:
    return max(1, math.ceil(len(line) / 4))


def render_tokens(text: str) -> int:
    return sum(line_tokens(line) for line in text.split("\n"))
