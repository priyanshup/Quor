"""path_prefix_fold stage: front-code a run of path-like KEEP lines.

QB-095: path-heavy tool output (`find`, `rg --files`, `ls -R`, coverage
reports, repo maps, stack traces) commonly repeats a long shared directory
prefix on every line:

    src/quor/pipeline/stages/foo.py
    src/quor/pipeline/stages/bar.py
    src/quor/pipeline/stages/baz.py

This stage folds a consecutive run of such lines to:

    src/quor/pipeline/stages/ (3 entries):
    foo.py
    bar.py
    baz.py

Same idea as front-coding in sorted-string search-index term dictionaries
(and VCS/IDE path-tree UIs), applied to line-oriented tool output.

`patterns` — like `group_repeated`/`strip_lines`, the filter author declares
which KEEP lines are path-like via regex. There is no built-in "looks like a
path" heuristic: guessing that shape from arbitrary text risks folding lines
that only coincidentally contain `/`, and the project's existing stages all
put that judgment call on the filter author, not on internal sniffing.

Folding a run is a token-cost decision (same principle as
`collapse_unchanged_context`, QB-055), not a line-count threshold: a run
folds only when doing so is estimated to cost strictly fewer tokens than
leaving it as-is, so a run whose lines share only a trivially short prefix
(not worth a header line) is left untouched with no separate magic number to
tune.

The shared prefix is computed char-wise across the whole run, then trimmed
back to the last `separator` boundary so a fold can never split a filename
mid-token (a naive string LCP of "collapse_unchanged_context.py" and
"code_ast_summarize.py" would cut at "co", which is wrong). Scope is
deliberately single-level: one shared prefix per run, not a multi-level
directory tree — nesting is a possible future enhancement, not attempted
here.

Losslessness: unlike `group_repeated`'s "(xN)" collapsing (which relies on
genuine duplication and COMPRESSes the redundant lines away), this stage
never discards a line's content. Every original line is reconstructible as
header-prefix + its own rewritten suffix, byte-for-byte. That is also why
this stage is allowed to rewrite every line in a matched run instead of just
the first (see quor/pipeline/mask.py's module docstring) — a small, distinct
category of exception from group_repeated/collapse_unchanged_context, not a
precedent for arbitrary multi-line rewriting.
"""

from __future__ import annotations

import math
from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig


class PathPrefixFoldConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: list[str] = Field(default_factory=list)
    separator: str = "/"


class PathPrefixFoldStage:
    """Front-code consecutive path-like KEEP lines sharing a directory prefix."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "path_prefix_fold"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, PathPrefixFoldConfig):
            raise TypeError(
                f"path_prefix_fold requires PathPrefixFoldConfig, got {type(config).__name__}"
            )

        if not config.patterns or not config.separator:
            return mask

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        compiled_patterns = [_compile(p) for p in config.patterns]

        lines = list(mask.lines)

        if compiled_preserve:
            lines = [
                LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                if lm.decision is not Decision.PROTECT and matches_any(lm.line, compiled_preserve)
                else lm
                for lm in lines
            ]

        result: list[LineMask] = []
        i = 0
        n = len(lines)

        while i < n:
            lm = lines[i]
            if lm.decision is not Decision.KEEP or not matches_any(lm.line, compiled_patterns):
                result.append(lm)
                i += 1
                continue

            j = i
            run: list[LineMask] = []
            while (
                j < n
                and lines[j].decision is Decision.KEEP
                and matches_any(lines[j].line, compiled_patterns)
            ):
                run.append(lines[j])
                j += 1

            result.extend(_fold_run(run, config.separator, self.stage_type))
            i = j

        return ContentMask(tuple(result))


def _line_tokens(line: str) -> int:
    """Estimate a line's token cost: ceil(len(line) / 4), same as `max_tokens`
    and `collapse_unchanged_context`."""
    return max(1, math.ceil(len(line) / 4))


def _common_prefix(lines: list[str]) -> str:
    if not lines:
        return ""
    prefix = lines[0]
    for line in lines[1:]:
        limit = min(len(prefix), len(line))
        k = 0
        while k < limit and prefix[k] == line[k]:
            k += 1
        prefix = prefix[:k]
        if not prefix:
            return ""
    return prefix


def _fold_run(run: list[LineMask], separator: str, stage_type: str) -> list[LineMask]:
    """Fold `run` (2+ consecutive matching KEEP LineMasks) to a header line
    plus each line rewritten to its shared-prefix-stripped suffix, if doing
    so is estimated to cost strictly fewer tokens than leaving it as-is."""
    if len(run) < 2:
        return run

    prefix = _common_prefix([lm.line for lm in run])
    cut = prefix.rfind(separator)
    if cut == -1:
        return run
    prefix = prefix[: cut + len(separator)]
    if not prefix:
        return run

    header_text = f"{prefix} ({len(run)} entries):"
    original_cost = sum(_line_tokens(lm.line) for lm in run)
    folded_cost = _line_tokens(header_text) + sum(
        _line_tokens(lm.line[len(prefix) :]) for lm in run
    )
    if folded_cost >= original_cost:
        return run

    header = LineMask(
        line=header_text,
        decision=Decision.KEEP,
        reason=f"folded {len(run)} lines sharing prefix {prefix!r}",
        stage=stage_type,
    )
    children = [
        LineMask(
            line=lm.line[len(prefix) :],
            decision=Decision.KEEP,
            reason="path prefix folded",
            stage=stage_type,
        )
        for lm in run
    ]
    return [header, *children]
