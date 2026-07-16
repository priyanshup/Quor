"""QB-054 — flags derived from per-filter real-usage stats
(`quor.tracking.db.FilterUsage`): filters whose real compression is
negative or near-zero, and filters whose real usage/compression diverges
from the benchmark corpus (`quor.analytics.filter_baseline`).

Presentation/analysis only — every number here already exists in
`FilterUsage`/`BenchmarkFilterStats`; nothing is computed for the first
time in this module, and nothing here changes what any filter does.
"""

from __future__ import annotations

from dataclasses import dataclass

from quor.analytics.filter_baseline import BenchmarkFilterStats
from quor.tracking.db import PASSTHROUGH_LABEL, FilterUsage

# A filter below this real compression_pct (including negative, i.e. net
# expansion) is flagged — generalizes the QB-052 mypy/npm finding
# ("46.1% benchmark vs -41.2% real") into a standing, reusable threshold
# rather than a one-off manual check.
NEAR_ZERO_COMPRESSION_PCT = 5.0


@dataclass(frozen=True)
class LowPerformer:
    filter_name: str
    avg_compression_pct: float
    invocation_count: int


def flag_low_performers(
    filters: tuple[FilterUsage, ...],
    *,
    threshold_pct: float = NEAR_ZERO_COMPRESSION_PCT,
) -> list[LowPerformer]:
    """Real filters (the `PASSTHROUGH_LABEL` bucket is excluded — it isn't a
    filter, and its `avg_compression_pct` is always exactly 0.0 by
    construction, which would otherwise always show up here) whose real
    `avg_compression_pct` is below `threshold_pct`. Sorted worst first."""
    flagged = [
        LowPerformer(f.filter_name, f.avg_compression_pct, f.invocation_count)
        for f in filters
        if f.filter_name != PASSTHROUGH_LABEL and f.avg_compression_pct < threshold_pct
    ]
    return sorted(flagged, key=lambda f: f.avg_compression_pct)


@dataclass(frozen=True)
class UsageDivergence:
    filter_name: str
    real_usage_pct: float
    benchmark_usage_pct: float
    usage_delta_pp: float
    real_compression_pct: float
    benchmark_compression_pct: float
    compression_delta_pp: float


def compute_divergence(
    filters: tuple[FilterUsage, ...],
    benchmark: dict[str, BenchmarkFilterStats],
) -> list[UsageDivergence]:
    """Compare each real filter against its benchmark-corpus counterpart,
    matched by name. Only filters present on both sides are comparable — a
    filter with real invocations but no benchmark case (or vice versa) has
    nothing to diverge against, so it's omitted rather than shown with a
    fabricated zero. Sorted by largest compression divergence first (the
    dimension the 2026-07-15 product-strategy review's own findings —
    mypy/git-log/git-status/pytest — were all expressed in)."""
    diverged = [
        UsageDivergence(
            filter_name=f.filter_name,
            real_usage_pct=f.usage_pct,
            benchmark_usage_pct=b.usage_pct,
            usage_delta_pp=f.usage_pct - b.usage_pct,
            real_compression_pct=f.avg_compression_pct,
            benchmark_compression_pct=b.compression_pct,
            compression_delta_pp=f.avg_compression_pct - b.compression_pct,
        )
        for f in filters
        if f.filter_name != PASSTHROUGH_LABEL
        for b in [benchmark.get(f.filter_name)]
        if b is not None
    ]
    return sorted(diverged, key=lambda d: abs(d.compression_delta_pp), reverse=True)
