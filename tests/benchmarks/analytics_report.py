"""QB-039 — compression analytics report: stage/language contribution,
hardest files, and per-stage effectiveness rating.

Presentation + light aggregation only, exactly like `report.py` next to it:
every number here comes from `AggregateSummary`/`BenchmarkResult`
(benchmark_runner.py, unchanged) or `quor.analytics` (stage_stats.py,
effectiveness.py — also unchanged by this module). Nothing here computes a
compression number; it only groups and formats ones that already exist.

`stage_contribution()`'s percentages are each stage's share of the *total
tokens saved by tracked stage executions* — a different denominator than
`AggregateSummary.overall_compression_pct` (which is against total tokens
*before*), so stage-contribution rows are not expected to sum to the
headline "Overall" percentage; they sum to 100% of the measured savings
instead, which is the more useful reading for "which stage did the work."
"""

from __future__ import annotations

from typing import Any

from quor.analytics.effectiveness import EffectivenessRating, classify
from quor.analytics.stage_stats import StageStats, StageStatsCollector
from tests.benchmarks.benchmark_runner import AggregateSummary, BenchmarkResult

_BAR_WIDTH = 40


def collect_stage_stats(results: list[BenchmarkResult]) -> dict[str, StageStats]:
    """Fold every case's `stage_results` trace into one collector."""
    collector = StageStatsCollector()
    for r in results:
        collector.add_all(r.stage_results)
    return collector.snapshot()


def _dotted_row(label: str, pct: float) -> str:
    """Render one `label .......... NN%` row, ticket-style (§2's example)."""
    dots = max(1, _BAR_WIDTH - len(label))
    return f"{label} {'.' * dots} {pct:.0f}%"


def render_overall(summary: AggregateSummary) -> list[str]:
    lines = ["Overall", "-" * 8, "", f"{summary.overall_compression_pct:.1f}%", ""]
    return lines


def render_stage_contribution(stage_stats: dict[str, StageStats]) -> list[str]:
    total_saved = sum(s.tokens_saved for s in stage_stats.values())
    lines = ["Stage contribution", "-" * 19, ""]
    if not stage_stats:
        lines.append("(no stage traces captured)")
        lines.append("")
        return lines
    ranked = sorted(stage_stats.values(), key=lambda s: s.tokens_saved, reverse=True)
    for s in ranked:
        pct = (s.tokens_saved / total_saved * 100) if total_saved else 0.0
        lines.append(_dotted_row(s.stage_type, pct))
    lines.append("")
    return lines


def render_language_contribution(per_ecosystem: dict[str, dict[str, Any]]) -> list[str]:
    lines = ["Language contribution", "-" * 22, ""]
    for name, stats in sorted(per_ecosystem.items()):
        lines.append(_dotted_row(name, float(stats["compression_pct"])))
    lines.append("")
    return lines


def render_top_hardest_files(results: list[BenchmarkResult], n: int = 10) -> list[str]:
    """Lowest `compression_pct` first — the cases that resist compression most."""
    ranked = sorted(results, key=lambda r: r.compression_pct)[:n]
    lines = [f"Top {len(ranked)} hardest files", "-" * (11 + len(str(len(ranked)))), ""]
    for i, r in enumerate(ranked, start=1):
        lines.append(f"{i:>2}. {r.id} - {r.compression_pct:.1f}% ({r.category}/{r.ecosystem})")
    lines.append("")
    return lines


def render_effectiveness(ratings: list[EffectivenessRating]) -> list[str]:
    lines = ["Compression effectiveness (by measured contribution)", "-" * 54, ""]
    if not ratings:
        lines.append("(no stage traces captured)")
        lines.append("")
        return lines
    header = f"{'Stage':<28}{'Impact':<8}{'Contribution':<14}{'Activation':<12}{'Avg saved':<10}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in ratings:
        lines.append(
            f"{r.stage_type:<28}{r.impact:<8}{r.total_contribution_pct:>10.1f}%   "
            f"{r.activation_rate_pct:>8.1f}%   {r.avg_savings_pct:>7.1f}%"
        )
    lines.append("")
    return lines


def render_analytics_report(
    results: list[BenchmarkResult],
    summary: AggregateSummary,
) -> str:
    """Assemble the full QB-039 analytics report as one plain-text block."""
    stage_stats = collect_stage_stats(results)
    ratings = classify(stage_stats)

    lines: list[str] = []
    lines += render_overall(summary)
    lines += render_stage_contribution(stage_stats)
    lines += render_language_contribution(summary.per_ecosystem)
    lines += render_top_hardest_files(results)
    lines += render_effectiveness(ratings)
    return "\n".join(lines).rstrip() + "\n"
