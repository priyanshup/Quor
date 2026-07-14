"""Stage impact classification (QB-039 Deliverable 3).

Turns `StageStats` (quor.analytics.stage_stats) into a High/Medium/Low
impact rating per stage, so future roadmap work can be prioritized against
measured contribution instead of intuition. Presentation/analysis only —
never runs a pipeline, never changes what any stage does.

Impact is decided by one explicit axis, `total_contribution_pct` (this
stage's share of every measured token saved across the whole run) — not a
blended composite score. `activation_rate` and `avg_savings_pct` are
reported alongside as supporting evidence rather than folded into the
threshold math, so a reader can see *why* a rating was assigned (e.g. "high
average savings but almost never activates" vs. "modest average savings but
fires on nearly everything") instead of trusting an opaque single number.
This also makes the two thresholds below easy to revisit independently as
real data comes in.
"""

from __future__ import annotations

from dataclasses import dataclass

from quor.analytics.stage_stats import StageStats

HIGH_CONTRIBUTION_PCT = 15.0
MEDIUM_CONTRIBUTION_PCT = 5.0


@dataclass(frozen=True)
class EffectivenessRating:
    stage_type: str
    impact: str  # "High" | "Medium" | "Low"
    total_contribution_pct: float
    activation_rate_pct: float
    avg_savings_pct: float
    executions: int
    skipped: int
    failed: int


def _classify(total_contribution_pct: float) -> str:
    if total_contribution_pct >= HIGH_CONTRIBUTION_PCT:
        return "High"
    if total_contribution_pct >= MEDIUM_CONTRIBUTION_PCT:
        return "Medium"
    return "Low"


def classify(stats: dict[str, StageStats]) -> list[EffectivenessRating]:
    """Rate every stage in `stats` (a `StageStatsCollector.snapshot()`).

    Returned list is sorted by `total_contribution_pct` descending — the
    same ordering the ticket's "High/Medium/Low impact" report wants to
    read top to bottom.
    """
    grand_total_saved = sum(s.tokens_saved for s in stats.values())

    ratings = [
        EffectivenessRating(
            stage_type=s.stage_type,
            impact=_classify(
                (s.tokens_saved / grand_total_saved * 100) if grand_total_saved else 0.0
            ),
            total_contribution_pct=(
                (s.tokens_saved / grand_total_saved * 100) if grand_total_saved else 0.0
            ),
            activation_rate_pct=s.activation_rate * 100,
            avg_savings_pct=s.avg_pct,
            executions=s.executions,
            skipped=s.skipped,
            failed=s.failed,
        )
        for s in stats.values()
    ]
    return sorted(ratings, key=lambda r: r.total_contribution_pct, reverse=True)
