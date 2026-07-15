"""collapse_unchanged_context stage: collapse long runs of unchanged KEEP lines.

Built for QB-041 (git-diff compression): today's git-diff filter marks every
`+`/`-`/`@@` line PROTECT via `preserve_patterns`, which is correct and
unchanged by this stage — but it leaves long runs of ordinary unified-diff
context lines (no `+`/`-` prefix) as plain KEEP, with nothing to compress them
on large diffs. This stage collapses the *middle* of such a run, keeping a
small window of context immediately adjacent to each PROTECT/COMPRESS
boundary (mirroring `git diff -U<n>`'s instinct, decided after the fact).

Only ever touches lines already decided KEEP by earlier stages. PROTECT lines
(edits, hunk headers, conflict markers, per ADR-031) and COMPRESS lines are
run boundaries, never modified — the same "never downgrade PROTECT" guarantee
every other stage honors.

QB-055: the decision to collapse a run's middle is a token-cost comparison
(estimated middle tokens vs. estimated placeholder tokens), not a line-count
threshold — a fixed count either fires too rarely on short, token-dense lines
or fires needlessly on long runs of trivially short lines. Token estimation
uses the same char/4 approximation as `max_tokens` (see that stage's own
docstring).

Like `group_repeated`, this stage rewrites the content of one line per
collapsed run (the placeholder) rather than only toggling decisions — the
`mask.py` "sole exception" note covers both stages.
"""

from __future__ import annotations

import math
from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages.base import StageConfig


class CollapseUnchangedContextConfig(StageConfig):
    """
    context_lines — how many unchanged lines to keep immediately before and
    after each protected/edited region, on each side of a collapsed run.

    Whether the middle of a run is collapsed at all is decided by estimated
    token cost, not a line count: the middle is only collapsed when its
    estimated token cost is strictly greater than the placeholder's estimated
    token cost. Ties (equal cost) are left uncollapsed — conservative by
    design, never make output larger or equal in estimated size.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_lines: int = Field(default=3, ge=0)


class CollapseUnchangedContextStage:
    """Collapse the middle of long unchanged-context runs into a placeholder."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "collapse_unchanged_context"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, CollapseUnchangedContextConfig):
            raise TypeError(
                f"collapse_unchanged_context requires CollapseUnchangedContextConfig, "
                f"got {type(config).__name__}"
            )

        lines = list(mask.lines)
        result: list[LineMask] = []
        i = 0
        n = len(lines)

        while i < n:
            lm = lines[i]
            if lm.decision is not Decision.KEEP:
                result.append(lm)
                i += 1
                continue

            j = i
            while j < n and lines[j].decision is Decision.KEEP:
                j += 1

            result.extend(
                _collapse_run(lines[i:j], config.context_lines, self.stage_type)
            )
            i = j

        return ContentMask(tuple(result))


def _line_tokens(line: str) -> int:
    """Estimate a line's token cost: ceil(len(line) / 4), same as `max_tokens`."""
    return max(1, math.ceil(len(line) / 4))


def _collapse_run(run: list[LineMask], window: int, stage_type: str) -> list[LineMask]:
    """Collapse the middle of one run of consecutive KEEP lines, if doing so
    is estimated to cost strictly fewer tokens than leaving it as-is."""
    head = run[:window]
    tail = run[len(run) - window :] if window else []
    middle = run[window : len(run) - window]

    if not middle:
        return run

    placeholder_text = f"... {len(middle)} unchanged lines omitted ..."
    middle_cost = sum(_line_tokens(m.line) for m in middle)
    placeholder_cost = _line_tokens(placeholder_text)
    if placeholder_cost >= middle_cost:
        return run

    # Reuse the middle's first line as the placeholder (like group_repeated
    # reuses its run's first line) so total LineMask count is unchanged.
    placeholder = LineMask(
        line=placeholder_text,
        decision=Decision.KEEP,
        reason=f"collapsed {len(middle)} unchanged context lines",
        stage=stage_type,
    )
    compressed_rest = [
        LineMask(m.line, Decision.COMPRESS, "collapsed unchanged context", stage_type)
        for m in middle[1:]
    ]

    return [*head, placeholder, *compressed_rest, *tail]
