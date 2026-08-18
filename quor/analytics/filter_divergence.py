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
from quor.tracking.db import (
    MCP_DEDUP_FILTER_LABEL,
    MCP_REPO_CONTEXT_FILTER_LABEL,
    PASSTHROUGH_LABEL,
    REPO_DASHBOARD_FILTER_LABEL,
    REPO_EXPLORE_FILTER_LABEL,
    REPO_GRAPH_FILTER_LABEL,
    REPO_PROFILE_FILTER_LABEL,
    REPO_SEARCH_FILTER_LABEL,
    REPO_SYMBOLS_FILTER_LABEL,
    FilterUsage,
)

# Synthetic, non-ContentMask-filter labels that must never be flagged as
# "low performing" — each is documented at its own definition (PASSTHROUGH_LABEL
# in quor/tracking/db.py, REPO_PROFILE_FILTER_LABEL/REPO_SYMBOLS_FILTER_LABEL/
# REPO_GRAPH_FILTER_LABEL/REPO_DASHBOARD_FILTER_LABEL/REPO_EXPLORE_FILTER_LABEL/
# REPO_SEARCH_FILTER_LABEL/MCP_REPO_CONTEXT_FILTER_LABEL alongside it) as
# always reading 0.0%/near-zero by design, not as evidence of a broken
# filter. MCP_DEDUP_FILTER_LABEL is excluded for a different reason (see its
# own docstring in quor/tracking/db.py) — it's real savings, not a synthesis
# label, but a session-cache hit rate has no benchmark counterpart to
# meaningfully diverge against, same as PASSTHROUGH_LABEL.
_EXCLUDED_FROM_LOW_PERFORMER_CHECK = frozenset(
    {
        PASSTHROUGH_LABEL,
        REPO_PROFILE_FILTER_LABEL,
        REPO_SYMBOLS_FILTER_LABEL,
        REPO_GRAPH_FILTER_LABEL,
        REPO_DASHBOARD_FILTER_LABEL,
        REPO_EXPLORE_FILTER_LABEL,
        REPO_SEARCH_FILTER_LABEL,
        MCP_REPO_CONTEXT_FILTER_LABEL,
        MCP_DEDUP_FILTER_LABEL,
    }
)

# A filter below this real compression_pct (including negative, i.e. net
# expansion) is flagged — generalizes the QB-052 mypy/npm finding
# ("46.1% benchmark vs -41.2% real") into a standing, reusable threshold
# rather than a one-off manual check.
NEAR_ZERO_COMPRESSION_PCT = 5.0


@dataclass(frozen=True)
class LowPerformer:
    filter_name: str
    avg_compression_pct: float
    per_invocation_avg_pct: float
    invocation_count: int


def flag_low_performers(
    filters: tuple[FilterUsage, ...],
    *,
    threshold_pct: float = NEAR_ZERO_COMPRESSION_PCT,
) -> list[LowPerformer]:
    """Real filters (synthetic, non-ContentMask-filter labels —
    `PASSTHROUGH_LABEL`, `REPO_PROFILE_FILTER_LABEL`, and
    `REPO_SYMBOLS_FILTER_LABEL` — are excluded; none is a real filter, and
    each has an `avg_compression_pct` that is always exactly 0.0 by
    construction, which would otherwise always show up here) whose real
    `avg_compression_pct` is below `threshold_pct`, OR whose
    `per_invocation_avg_pct` is negative even when the aggregate ratio
    isn't.

    QB-065: the aggregate-only version of this check (compare
    `avg_compression_pct` alone) has a real, confirmed blind spot — a
    handful of very large invocations can carry a positive aggregate ratio
    while most real invocations are still individually net-negative. This
    was found directly in production: after QB-052's tee-footer fix,
    `ruff`/`generic` still showed this shape *before* the fix (aggregate
    +20.8%/+43.8%, comfortably above `NEAR_ZERO_COMPRESSION_PCT`, while
    `per_invocation_avg_pct` was -12.3%/-2.8%) — the original,
    aggregate-only version of this function would not have flagged either
    one. Both signals are still reported (not just the worse of the two, to
    keep the aggregate/mean divergence itself visible to a reader — see
    `quor/analytics/filter_report.py::render_low_performers`), and sorted by
    whichever is worse so the clearest problem sorts first.
    """
    flagged = [
        LowPerformer(f.filter_name, f.avg_compression_pct, f.per_invocation_avg_pct, f.invocation_count)
        for f in filters
        if f.filter_name not in _EXCLUDED_FROM_LOW_PERFORMER_CHECK
        and (f.avg_compression_pct < threshold_pct or f.per_invocation_avg_pct < 0)
    ]
    return sorted(flagged, key=lambda f: min(f.avg_compression_pct, f.per_invocation_avg_pct))


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
    fabricated zero (see `find_uncovered_filters()` below for that case
    specifically). Sorted by largest compression divergence first (the
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


# QB-047 Phase 1 — evidence-directed benchmark curation. Neither function
# below collects, stores, or transmits anything new: both are pure filters/
# sorts over the exact same `FilterUsage`/`BenchmarkFilterStats` values
# `compute_divergence()`/`flag_low_performers()` already read. The intent is
# to make "which filter should get a new hand-written benchmark sample
# next" a directly answerable question from already-shipped QB-054 data,
# instead of a one-off manual comparison (as the 2026-07-15 product-strategy
# review had to do) — never automatic corpus generation, never real content.
# A gap this surfaces is a *nomination*: a human still authors, reviews, and
# commits the actual new `[[case]]`/sample file, following
# `tests/benchmarks/README.md`'s existing "Adding a new benchmark case"
# process.

# A real-usage/benchmark compression gap at or beyond this many percentage
# points is a nomination candidate. Chosen from the 2026-07-15 review's own
# confirmed findings (docs/BENCHMARKS.md's "Real-world vs. benchmark
# observations" table) — every filter actually flagged there diverged by at
# least ~13pp (git-status, the smallest of the five), most by 30-90pp — so
# 15.0 catches that entire confirmed class without being so low it flags
# routine sample-to-sample noise.
DEFAULT_NOMINATION_THRESHOLD_PP = 15.0


def find_uncovered_filters(
    filters: tuple[FilterUsage, ...],
    benchmark: dict[str, BenchmarkFilterStats],
) -> list[FilterUsage]:
    """Real filters with production usage but **zero** benchmark-corpus
    coverage — the case `compute_divergence()` deliberately omits (nothing
    to diverge against). Arguably the single best nomination signal: a
    filter with real invocations and no benchmark case at all has no
    regression protection whatsoever, which a large-but-covered divergence
    at least has *some* baseline for. Excludes `PASSTHROUGH_LABEL` (not a
    filter) and every `SYNTHESIS_FILTER_LABELS` entry (`quor map`/`symbols`/
    `graph`/`repo`/`explore`/`search` — synthesis commands with no
    benchmark counterpart by design, not a coverage gap). Sorted by usage
    share descending — the most-used uncovered filter is the best-evidenced
    nomination.
    """
    uncovered = [
        f
        for f in filters
        if f.filter_name != PASSTHROUGH_LABEL
        and f.filter_name not in _EXCLUDED_FROM_LOW_PERFORMER_CHECK
        and f.filter_name not in benchmark
    ]
    return sorted(uncovered, key=lambda f: f.usage_pct, reverse=True)


def nominate_for_benchmark_coverage(
    diffs: list[UsageDivergence],
    *,
    threshold_pp: float = DEFAULT_NOMINATION_THRESHOLD_PP,
) -> list[UsageDivergence]:
    """Narrow `compute_divergence()`'s already-sorted output down to entries
    whose compression divergence clears `threshold_pp` — the filters worth
    actually nominating for new hand-written benchmark samples, rather than
    every filter with any measurable gap at all. Already sorted by largest
    absolute divergence first (inherited from `diffs`' own ordering)."""
    return [d for d in diffs if abs(d.compression_delta_pp) >= threshold_pp]
