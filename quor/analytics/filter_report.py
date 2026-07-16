"""QB-054 — plain-text per-filter real-usage analytics report.

Assembles `quor.tracking.db.FilterAnalyticsReport` plus
`quor.analytics.filter_baseline`/`filter_divergence`/`filter_history` into
the views QB-054 asks for. Mirrors `tests/benchmarks/analytics_report.py`'s
own "presentation + light aggregation only" contract: every number here
already exists in one of those modules; nothing is computed for the first
time here.
"""

from __future__ import annotations

from quor.analytics.filter_baseline import BenchmarkFilterStats, load_benchmark_filter_stats
from quor.analytics.filter_divergence import compute_divergence, flag_low_performers
from quor.analytics.filter_history import AnalyticsHistoryEntry, growing_filters
from quor.tracking.db import PASSTHROUGH_LABEL, FilterAnalyticsReport, FilterUsage

_TOP_N = 10


def _real_filters(report: FilterAnalyticsReport) -> list[FilterUsage]:
    """Every group except the synthetic passthrough bucket — "filters" in
    every section below means filters that actually ran, not the absence
    of one."""
    return [f for f in report.filters if f.filter_name != PASSTHROUGH_LABEL]


def render_top_used(report: FilterAnalyticsReport, n: int = _TOP_N) -> list[str]:
    ranked = sorted(_real_filters(report), key=lambda f: f.invocation_count, reverse=True)[:n]
    lines = [f"Top {len(ranked)} most-used filters", "-" * 30, ""]
    if not ranked:
        lines.append("(no filter invocations in this window)")
    for i, f in enumerate(ranked, start=1):
        lines.append(
            f"{i:>2}. {f.filter_name:<20} {f.invocation_count:>5} calls ({f.usage_pct:.1f}%)"
        )
    lines.append("")
    return lines


def render_top_compression(report: FilterAnalyticsReport, n: int = _TOP_N) -> list[str]:
    ranked = sorted(_real_filters(report), key=lambda f: f.avg_compression_pct, reverse=True)[:n]
    lines = ["Top compression performers", "-" * 27, ""]
    if not ranked:
        lines.append("(no filter invocations in this window)")
    for i, f in enumerate(ranked, start=1):
        lines.append(f"{i:>2}. {f.filter_name:<20} {f.avg_compression_pct:>6.1f}%")
    lines.append("")
    return lines


def render_worst_compression(report: FilterAnalyticsReport, n: int = _TOP_N) -> list[str]:
    ranked = sorted(_real_filters(report), key=lambda f: f.avg_compression_pct)[:n]
    lines = ["Worst compression performers", "-" * 29, ""]
    if not ranked:
        lines.append("(no filter invocations in this window)")
    for i, f in enumerate(ranked, start=1):
        lines.append(f"{i:>2}. {f.filter_name:<20} {f.avg_compression_pct:>6.1f}%")
    lines.append("")
    return lines


def render_low_performers(report: FilterAnalyticsReport) -> list[str]:
    flagged = flag_low_performers(report.filters)
    lines = ["Negative or near-zero compression", "-" * 34, ""]
    if not flagged:
        lines.append("(none)")
    for f in flagged:
        lines.append(
            f"{f.filter_name:<20} {f.avg_compression_pct:>6.1f}%   ({f.invocation_count} calls)"
        )
    lines.append("")
    return lines


def render_divergence(
    report: FilterAnalyticsReport,
    benchmark: dict[str, BenchmarkFilterStats] | None = None,
) -> list[str]:
    if benchmark is None:
        benchmark = load_benchmark_filter_stats()
    diffs = compute_divergence(report.filters, benchmark)
    lines = ["Real usage vs benchmark divergence", "-" * 35, ""]
    if not diffs:
        lines.append("(no overlapping filters between real usage and the benchmark corpus)")
        lines.append("")
        return lines
    for d in diffs:
        lines.append(
            f"{d.filter_name:<20} compression: real {d.real_compression_pct:>6.1f}% "
            f"vs benchmark {d.benchmark_compression_pct:>6.1f}% "
            f"({d.compression_delta_pp:+.1f}pp)   "
            f"usage: real {d.real_usage_pct:>5.1f}% vs benchmark {d.benchmark_usage_pct:>5.1f}%"
        )
    lines.append("")
    return lines


def render_growth(history: list[AnalyticsHistoryEntry]) -> list[str]:
    growth = growing_filters(history)
    lines = ["Filters growing over time", "-" * 26, ""]
    if len(history) < 2:
        lines.append("(need at least two analytics snapshots to detect a trend)")
    elif not growth:
        lines.append("(no filter's usage share increased between the oldest and newest snapshot)")
    else:
        for g in growth:
            lines.append(
                f"{g.filter_name:<20} {g.oldest_usage_pct:>5.1f}% -> "
                f"{g.newest_usage_pct:>5.1f}% ({g.growth_pp:+.1f}pp)"
            )
    lines.append("")
    return lines


def render_filter_analytics_report(
    report: FilterAnalyticsReport,
    history: list[AnalyticsHistoryEntry],
    benchmark: dict[str, BenchmarkFilterStats] | None = None,
) -> str:
    """Assemble the full QB-054 per-filter analytics report as one
    plain-text block, in the order the ticket lists its required views."""
    lines: list[str] = [
        f"Per-filter analytics (last {report.days} days, "
        f"{report.total_invocations} invocations)",
        "=" * 60,
        "",
    ]
    lines += render_top_used(report)
    lines += render_top_compression(report)
    lines += render_worst_compression(report)
    lines += render_low_performers(report)
    lines += render_divergence(report, benchmark)
    lines += render_growth(history)
    return "\n".join(lines).rstrip() + "\n"
