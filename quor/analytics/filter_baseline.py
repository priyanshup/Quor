"""QB-054 — per-filter stats from the benchmark corpus, for comparison
against real usage (`quor.tracking.db.query_filter_analytics`).

Reads `tests/benchmarks/baseline.json`'s flat case list — the git-tracked,
stable corpus ground truth (`tests/benchmarks/results/*.json` is generated
and gitignored, so it isn't a reliable source to depend on here) — and
aggregates it by `matched_filter`, the same field
`tests/benchmarks/benchmark_runner.py` already populates with
`filter_config.name`, i.e. the identical string `quor.tracking.db`'s
`filter_name` column stores. Pure read + aggregate: this module never runs
a pipeline or a benchmark case.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

DEFAULT_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "tests" / "benchmarks" / "baseline.json"
)


@dataclass(frozen=True)
class BenchmarkFilterStats:
    """One filter's share of the benchmark corpus."""

    filter_name: str
    case_count: int
    usage_pct: float          # case_count / total_cases * 100
    compression_pct: float    # sum(saved) / sum(original) * 100 across this filter's cases


def load_benchmark_filter_stats(
    path: Path = DEFAULT_BASELINE_PATH,
) -> dict[str, BenchmarkFilterStats]:
    """Group `baseline.json`'s cases by `matched_filter` and return one
    `BenchmarkFilterStats` per filter name. Returns `{}` if the baseline
    file is missing or empty — the same fail-open shape every other
    analytics read in this project uses, so a fresh checkout without the
    benchmark suite still runs, just with an empty comparison set."""
    if not path.exists():
        return {}

    data = orjson.loads(path.read_bytes())
    results: list[dict[str, Any]] = data.get("results", [])
    total_cases = len(results)
    if not total_cases:
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in results:
        name = case.get("matched_filter")
        if not name:
            continue
        grouped.setdefault(name, []).append(case)

    stats: dict[str, BenchmarkFilterStats] = {}
    for name, cases in grouped.items():
        original = sum(int(c["original_tokens"]) for c in cases)
        final = sum(int(c["final_tokens"]) for c in cases)
        stats[name] = BenchmarkFilterStats(
            filter_name=name,
            case_count=len(cases),
            usage_pct=len(cases) / total_cases * 100,
            compression_pct=((original - final) / original * 100) if original else 0.0,
        )
    return stats
