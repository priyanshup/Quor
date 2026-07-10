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

QB-005B: the actual `ast` parsing/line-range logic (formerly
`_compressible_body_lines()`/`_body_line_range()`, defined in this module)
has moved, unmodified, to `quor/pipeline/ast_summarize/python.py` and is now
reached through `quor/pipeline/ast_summarize/registry.py::get_analyzer()` —
the reusable, multi-language parser framework
`docs/design/QB-005A-ast-summarization-design.md` designs. This stage is now
a thin, Python-specific wrapper that delegates to that shared framework
instead of calling `ast.parse()` directly; `quor/pipeline/stages/
code_ast_summarize.py` (the new, generic, filter-configurable stage
QB-005B also introduces) delegates to the exact same analyzer via the exact
same registry lookup, so there is only ever one implementation of Python's
body-compression logic, not two. This stage's own class name, `stage_type`
("python_ast_summarize"), config shape, and every observable behavior are
unchanged — `cat-python.toml` requires no changes, and every pre-existing
test in `tests/unit/test_stages.py::TestPythonAstSummarize` passes
unmodified against this refactor (see backlog.md's QB-005B entry for the
explicit before/after equivalence proof).

`ast` is used for PARSING ONLY. This stage never regenerates, reformats, or
rewrites source text (no `ast.unparse()`, no reformatting of kept lines) —
every kept line is the original line, byte-for-byte. Compressed lines are
marked COMPRESS (dropped at render, per every other stage's convention);
nothing is ever replaced or synthesized, unlike group_repeated's repeat-count
marker — this stage follows the more common "silent drop" pattern most
built-in stages already use.

Fail-open: a SyntaxError (or any other ast.parse() failure — a null byte, a
file that isn't actually Python) is deliberately NOT caught here, nor in
`analyze_python()`/`get_analyzer()` (see those modules' own docstrings for
why the framework's fail-open contract deliberately differs from
`quor/pipeline/extract`'s). It propagates to Pipeline.execute()'s existing
per-stage fail-open handling, which keeps the mask exactly as it was before
this stage ran — i.e. the original file, completely unchanged, with a
warning logged. This mirrors every other stage's convention: only per-line,
expected failure modes (like a regex timeout) are caught locally; a
whole-stage failure relies on the engine's existing, already-tested
fail-open guarantee rather than a second, redundant try/except here.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.ast_summarize.registry import get_analyzer
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
        # get_analyzer("python") is always registered (see
        # quor/pipeline/ast_summarize/registry.py) — unlike code_ast_summarize,
        # this stage is Python-specific and never driven by a `language`
        # config field, so "python" is never a runtime unknown here.
        # The analyzer itself raises SyntaxError/ValueError on anything it
        # can't parse. Deliberately not caught here — see module docstring
        # "Fail-open": Pipeline.execute() already handles this correctly.
        analyzer = get_analyzer("python")
        if analyzer is None:  # pragma: no cover - "python" is always registered
            raise RuntimeError(
                "python analyzer unexpectedly missing from ast_summarize registry"
            )
        compress_lines = analyzer("\n".join(lm.line for lm in mask.lines))
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
