"""Deterministic per-stage compression breakdown for one pipeline trace (QB-057).

Consumes the `StageResult` values a single `Pipeline.execute(track_tokens=True)`
/ `FilterRegistry.trace(..., track_tokens=True)` call already produced — no
pipeline re-execution, no extra rendering or token counting beyond what that
one call already performed (see `quor.pipeline.engine.Pipeline.execute`'s own
`track_tokens` docstring). Reuses `StageStatsCollector` (QB-039) to aggregate
same-`stage_type` stages occurring more than once in one pipeline (e.g. a
filter with two separate `group_repeated` blocks) into a single reported
line, rather than introducing a second, parallel aggregation implementation.

This module never runs a pipeline and never calls `count_tokens()` itself —
`final_tokens` is derived arithmetically from `original_tokens` and the same
per-stage `tokens_saved` values the breakdown lines are built from, relying
on the fact that consecutive stages' `tokens_before`/`tokens_after` telescope
(each stage's `tokens_before` equals the previous stage's `tokens_after`), so
"sum every stage's own saving" and "first tokens_before minus last
tokens_after" are mathematically identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from quor.analytics.stage_stats import StageStatsCollector
from quor.pipeline.stages.base import StageResult


@dataclass(frozen=True)
class CompressionLine:
    """One reported row: a stage_type and the tokens it saved (always != 0)."""

    stage_type: str
    tokens_saved: int


@dataclass(frozen=True)
class CompressionSummary:
    """A deterministic, self-consistent compression breakdown for one trace."""

    original_tokens: int
    final_tokens: int
    lines: tuple[CompressionLine, ...]

    @property
    def total_saved(self) -> int:
        """Always exactly `sum(line.tokens_saved for line in self.lines)` —
        both are derived from the same per-stage_type totals, never measured
        independently, so the two can never drift apart."""
        return self.original_tokens - self.final_tokens

    @property
    def saved_pct(self) -> float:
        if not self.original_tokens:
            return 0.0
        return self.total_saved / self.original_tokens * 100


def build_compression_summary(
    original_tokens: int,
    stage_results: tuple[StageResult, ...] | list[StageResult],
) -> CompressionSummary:
    """Build a deterministic, per-stage token-savings breakdown.

    `original_tokens` must already be the token count of the content before
    the first stage in `stage_results` ran (the caller already has this —
    e.g. `count_tokens(captured)` in `quor explain` — so it is never
    recomputed here).

    Stages that didn't run (`was_skipped`) or raised (`error`) contribute
    zero, same as `StageStatsCollector` already treats them. Entries with
    untracked tokens (`tokens_before`/`tokens_after` is `None` — i.e. the
    caller didn't run the pipeline with `track_tokens=True`) also
    contribute zero. Same-`stage_type` stages occurring more than once are
    combined into one line, in first-appearance order (dict insertion order
    is preserved) — deterministic, and avoids a second, ambiguous "which
    instance is this" label when a filter uses the same stage type twice.

    A stage_type with exactly zero net effect is omitted from `lines`
    entirely (the task's "skip stages that saved zero tokens" requirement).
    Nothing else is filtered — the sum is over every remaining stage_type,
    so `original_tokens - final_tokens == sum(line.tokens_saved for line in
    lines)` holds by construction, not by convention.
    """
    collector = StageStatsCollector()
    collector.add_all(stage_results)

    lines = tuple(
        CompressionLine(stage_type=stage_type, tokens_saved=stats.tokens_saved)
        for stage_type, stats in collector.snapshot().items()
        if stats.tokens_saved != 0
    )
    total_saved = sum(line.tokens_saved for line in lines)

    return CompressionSummary(
        original_tokens=original_tokens,
        final_tokens=original_tokens - total_saved,
        lines=lines,
    )
