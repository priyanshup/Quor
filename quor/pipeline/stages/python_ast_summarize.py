"""python_ast_summarize stage: compress Python function/method bodies to
their signature + docstring, using the standard library `ast` module.

QB-005: reduces the token cost of `cat`-ing large Python files by
prioritizing imports, public types, function/method signatures, docstrings,
decorators, and file structure over full function bodies — the parts of a
file most useful for understanding its API surface without paying for every
implementation detail.

Python-only (V1): parses with stdlib `ast` — no third-party parser, no
tree-sitter, no new dependency. Detecting "this is a Python file" happens at
the filter layer (cat-python.toml's match_command matches `cat *.py`), not
here — this stage receives only file content, exactly like every other
stage; it is never told the filename, and StageHandler's interface is not
modified.

`ast` is used for PARSING ONLY. This stage never regenerates, reformats, or
rewrites source text (no `ast.unparse()`, no reformatting of kept lines) —
every kept line is the original line, byte-for-byte. Compressed lines are
marked COMPRESS (dropped at render, per every other stage's convention);
nothing is ever replaced or synthesized, unlike group_repeated's repeat-count
marker — this stage follows the more common "silent drop" pattern most
built-in stages already use.

Fail-open: a SyntaxError (or any other ast.parse() failure — a null byte, a
file that isn't actually Python) is deliberately NOT caught here. It
propagates to Pipeline.execute()'s existing per-stage fail-open handling,
which keeps the mask exactly as it was before this stage ran — i.e. the
original file, completely unchanged, with a warning logged. This mirrors
every other stage's convention: only per-line, expected failure modes (like
a regex timeout) are caught locally; a whole-stage failure relies on the
engine's existing, already-tested fail-open guarantee rather than a second,
redundant try/except here.
"""

from __future__ import annotations

import ast
from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig


class PythonAstSummarizeConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PythonAstSummarizeStage:
    """Compress Python function/method bodies to signature + docstring only."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "python_ast_summarize"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, PythonAstSummarizeConfig):
            raise TypeError(
                f"python_ast_summarize requires PythonAstSummarizeConfig, "
                f"got {type(config).__name__}"
            )

        # Parse the full original line sequence (mask.lines), not mask.render():
        # render() drops any line an earlier stage already marked COMPRESS, which
        # would shift ast's line numbers out of alignment with mask.lines indices.
        # Reconstructing from mask.lines keeps a 1:1 index<->lineno mapping
        # regardless of what upstream stages already decided.
        #
        # ast.parse() raises SyntaxError/ValueError on anything it can't
        # parse. Deliberately not caught here — see module docstring
        # "Fail-open": Pipeline.execute() already handles this correctly.
        tree = ast.parse("\n".join(lm.line for lm in mask.lines))

        compress_lines = _compressible_body_lines(tree)
        if not compress_lines:
            return mask

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        new_lines: list[LineMask] = []
        for idx, lm in enumerate(mask.lines):
            line_number = idx + 1  # ast line numbers are 1-indexed

            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                continue

            if lm.decision is Decision.COMPRESS:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                continue

            if line_number in compress_lines:
                new_lines.append(
                    LineMask(lm.line, Decision.COMPRESS, "function/method body", self.stage_type)
                )
            else:
                new_lines.append(lm)

        return ContentMask(tuple(new_lines))


def _compressible_body_lines(tree: ast.Module) -> set[int]:
    """Return the 1-indexed line numbers that belong to a function/method
    BODY (excluding its own signature, decorators, and docstring) and are
    therefore eligible for compression.

    Only top-level functions/methods are considered independently: once a
    function is selected for body compression, its body is not descended
    into any further, so a nested function's lines are covered by its
    enclosing function's range and are never processed on their own (its
    signature is not specially preserved — a function nested inside
    another function is implementation detail of the outer one).
    """
    lines: set[int] = set()

    def visit_body(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines.update(_body_line_range(stmt))
                # Deliberately do not recurse into stmt.body — any nested
                # def/class is already inside the range just added.
            elif isinstance(stmt, ast.ClassDef):
                visit_body(stmt.body)
            elif isinstance(stmt, ast.If):
                # Conditionally-defined top-level functions/classes, e.g.
                # `if TYPE_CHECKING: ...` or a version-gated definition.
                visit_body(stmt.body)
                visit_body(stmt.orelse)
            elif isinstance(stmt, ast.Try):
                # try/except ImportError fallback definitions.
                visit_body(stmt.body)
                for handler in stmt.handlers:
                    visit_body(handler.body)
                visit_body(stmt.orelse)
                visit_body(stmt.finalbody)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                visit_body(stmt.body)

    visit_body(tree.body)
    return lines


def _body_line_range(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Lines belonging to `node`'s body, excluding its signature/decorators
    and any leading docstring.

    Empty for trivial bodies: a same-line definition (`def f(): return 1`,
    where the body starts on the signature's own line — ContentMask is
    line-based and cannot partially compress a single physical line), or a
    body that is only a docstring.
    """
    if node.end_lineno is None or not node.body:
        return set()

    first_stmt = node.body[0]
    docstring_present = (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
        and isinstance(first_stmt.value.value, str)
    )
    remaining = node.body[1:] if docstring_present else node.body
    if not remaining:
        return set()

    start = remaining[0].lineno
    if start <= node.lineno:
        # Same-line body (`def f(): return 1`): the body shares its line with
        # the signature. ContentMask is line-based, so this line can't be
        # partially compressed without touching the signature — leave it alone.
        return set()

    end = node.end_lineno
    if start > end:
        return set()
    return set(range(start, end + 1))
