"""Per-stage aggregate statistics (QB-039).

Consumes `StageResult` values from many pipeline runs (a benchmark suite
today; `quor explain`/real session traces are a natural future feed — see
backlog.md's QB-039 entry) and accumulates, per `stage_type`: how often it
ran, was skipped, or failed, and how many tokens it saved when it did run.

Pure aggregation only. This module never runs a pipeline, never computes a
mask, and never touches `quor.filters.registry` or `quor.pipeline.engine`
directly — it only reads the `StageResult` objects those already produce
(via `Pipeline.execute(track_tokens=True)`). Nothing here changes
compression behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quor.pipeline.stages.base import StageResult


@dataclass
class StageStats:
    """Accumulated counters for one `stage_type` across many pipeline runs."""

    stage_type: str
    executions: int = 0
    skipped: int = 0
    failed: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    # Per-execution compression_pct samples, only from runs where a
    # non-zero tokens_before was measured — the raw material for avg/min/max.
    _pct_samples: list[float] = field(default_factory=list, repr=False)

    @property
    def total_seen(self) -> int:
        """Every time this stage_type appeared in a trace, regardless of outcome."""
        return self.executions + self.skipped + self.failed

    @property
    def activation_rate(self) -> float:
        """Fraction (0..1) of appearances where the stage actually ran (not
        skipped/failed) — answers "which filters almost never trigger"."""
        return self.executions / self.total_seen if self.total_seen else 0.0

    @property
    def avg_pct(self) -> float:
        return sum(self._pct_samples) / len(self._pct_samples) if self._pct_samples else 0.0

    @property
    def min_pct(self) -> float:
        return min(self._pct_samples) if self._pct_samples else 0.0

    @property
    def max_pct(self) -> float:
        return max(self._pct_samples) if self._pct_samples else 0.0

    def record_execution(self, tokens_before: int, tokens_after: int) -> None:
        """Fold in one tracked (tokens_before/tokens_after both known) execution."""
        self.executions += 1
        self.tokens_before += tokens_before
        self.tokens_after += tokens_after
        self.tokens_saved += tokens_before - tokens_after
        if tokens_before:
            self._pct_samples.append((tokens_before - tokens_after) / tokens_before * 100)


class StageStatsCollector:
    """Accumulates `StageResult`s into per-`stage_type` `StageStats`.

    Classification rule (uses only fields `StageResult` already has, no new
    execution-state concept introduced):
      - `error` set               -> failed   (the stage raised)
      - `was_skipped` (no error)  -> skipped  (can_handle()==False or early exit)
      - otherwise                 -> executed

    Only executed entries with tokens tracked (`tokens_before`/`tokens_after`
    not None — i.e. the run used `track_tokens=True`) feed the token sums
    and percentage samples; untracked runs still count toward
    executions/skipped/failed.
    """

    def __init__(self) -> None:
        self._stats: dict[str, StageStats] = {}

    def add(self, result: StageResult) -> None:
        stats = self._stats.setdefault(result.stage_type, StageStats(stage_type=result.stage_type))

        if result.error:
            stats.failed += 1
            return
        if result.was_skipped:
            stats.skipped += 1
            return

        if result.tokens_before is None or result.tokens_after is None:
            # Executed, but this run didn't track tokens (track_tokens=False)
            # — still counts as an execution, just contributes nothing to
            # the token/percentage aggregates.
            stats.executions += 1
            return

        stats.record_execution(result.tokens_before, result.tokens_after)

    def add_all(self, results: list[StageResult] | tuple[StageResult, ...]) -> None:
        for r in results:
            self.add(r)

    def snapshot(self) -> dict[str, StageStats]:
        """Return the current per-stage stats, keyed by stage_type."""
        return dict(self._stats)
