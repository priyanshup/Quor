"""Compression benchmark engine (QB-011).

Isolated from production code by construction: this module only ever
*calls* Quor's existing, unmodified public surface —
`quor.filters.registry.FilterRegistry` (the same lookup/apply path the real
dispatcher uses), `quor.tracking.db.count_tokens` (the same token estimate
`quor gain` uses), `quor.pipeline.tee.content_hash` (a pure hash utility,
used read-only to detect whether tee *would* fire — never calling
`write_tee()`, so a benchmark run never touches the real tee cache), and
`quor.pipeline.extract.registry.extract` (QB-007E4, for `.docx`/`.pdf`
cases only — see `run_case()`'s own comment). No compression algorithm,
stage, or filter is modified, patched, or special-cased for benchmark
purposes.

Two independent signals, kept separate rather than blended into one score:
  - Correctness (expected_filter routing + must_contain survival) — a
    violation is always fatal, regardless of how much was saved. Silently
    dropping required content is worse than a smaller compression ratio.
  - Compression quality (tokens saved, compression %) — checked two ways:
    a loose per-case `min_reduction_pct` floor (catches a catastrophic
    break even before a baseline exists), and comparison against a saved
    baseline (the precise regression signal: "smaller than last time").

execution_time_ms is captured and reported for visibility only — it is
never part of the pass/fail gate, since wall-clock time is inherently
noisy across machines and CI runners and would make the suite flaky.
"""

from __future__ import annotations

import statistics
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quor.filters.registry import FilterRegistry
from quor.pipeline.tee import content_hash
from quor.tracking.db import count_tokens

BENCHMARKS_DIR = Path(__file__).parent
DEFAULT_MANIFEST = BENCHMARKS_DIR / "manifest.toml"
DEFAULT_BASELINE = BENCHMARKS_DIR / "baseline.json"

# Extensions routed through quor/pipeline/extract (QB-007E1/E2/E3) before
# filtering, mirroring quor/adapters/claude_read.py's own
# `_EXTRACTION_EXTENSIONS` constant — duplicated here rather than imported
# for the identical reason that one is adapter-local rather than derived
# from the extraction registry: this module decides what it will *attempt*
# to route through extraction, independent of what the extraction registry
# happens to support today.
_EXTRACTION_EXTENSIONS = frozenset({".docx", ".pdf"})

# Regression classification is based on compression_pct delta, in
# percentage points, not on tokens_saved directly — this keeps the
# threshold meaningful regardless of a sample's absolute size.
DEFAULT_REGRESSION_THRESHOLD_PP = 2.0


@dataclass(frozen=True)
class BenchmarkCase:
    """One parsed manifest entry.

    `category` and `ecosystem` are both arbitrary, manifest-declared
    strings — the runner and aggregator never know or care what values
    exist. Adding a case for a brand-new filter (npm, Docker, PDF, DOCX,
    Terraform, ...) is purely a manifest + sample-file change: give it
    whatever `category`/`ecosystem` names make sense and nothing in this
    module needs to change to group or report on it.
    """

    id: str
    category: str
    ecosystem: str
    command: str
    sample_file: str
    expected_filter: str
    min_reduction_pct: float
    must_contain: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkResult:
    """Outcome of running one BenchmarkCase through the real filter pipeline."""

    id: str
    category: str
    ecosystem: str
    command: str
    matched_filter: str | None
    filter_correct: bool
    original_tokens: int
    final_tokens: int
    tokens_saved: int
    compression_pct: float
    execution_time_ms: float
    tee_would_fire: bool
    missing_patterns: tuple[str, ...]
    min_reduction_met: bool

    @property
    def correctness_ok(self) -> bool:
        """True if this case violates no correctness requirement at all."""
        return self.filter_correct and not self.missing_patterns

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "ecosystem": self.ecosystem,
            "command": self.command,
            "matched_filter": self.matched_filter,
            "filter_correct": self.filter_correct,
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "tokens_saved": self.tokens_saved,
            "compression_pct": round(self.compression_pct, 2),
            "execution_time_ms": round(self.execution_time_ms, 3),
            "tee_would_fire": self.tee_would_fire,
            "missing_patterns": list(self.missing_patterns),
            "min_reduction_met": self.min_reduction_met,
            "correctness_ok": self.correctness_ok,
        }


@dataclass(frozen=True)
class ComparisonEntry:
    """Result of comparing one case's current run against a saved baseline."""

    id: str
    status: str  # "new" | "improvement" | "regression" | "unchanged"
    baseline_compression_pct: float | None
    current_compression_pct: float
    delta_pp: float | None  # percentage points; None when status == "new"


@dataclass(frozen=True)
class AggregateSummary:
    total_cases: int
    total_original_tokens: int
    total_final_tokens: int
    total_tokens_saved: int
    overall_compression_pct: float
    total_execution_time_ms: float
    per_category: dict[str, dict[str, Any]]
    per_ecosystem: dict[str, dict[str, Any]]
    best_performers: list[BenchmarkResult]
    worst_performers: list[BenchmarkResult]
    correctness_failures: list[BenchmarkResult]


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> list[BenchmarkCase]:
    with open(manifest_path, "rb") as fh:
        data = tomllib.load(fh)
    cases = []
    for raw in data.get("case", []):
        cases.append(
            BenchmarkCase(
                id=raw["id"],
                category=raw["category"],
                ecosystem=raw["ecosystem"],
                command=raw["command"],
                sample_file=raw["sample_file"],
                expected_filter=raw["expected_filter"],
                min_reduction_pct=float(raw.get("min_reduction_pct", 0.0)),
                must_contain=tuple(raw.get("must_contain", [])),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _find_filter_by_name(registry: FilterRegistry, name: str) -> Any:
    """Mirrors `quor/adapters/claude_read.py`'s own `_find_filter_by_name`
    helper exactly (same composition from `FilterRegistry.all_filters()`,
    same reasoning: `FilterRegistry` has no built-in "find by name" method,
    so this composes it from the existing public API rather than adding
    one). Duplicated, not imported, because `claude_read.py`'s copy is a
    module-private adapter helper, not part of any shared/public surface —
    importing a leading-underscore name across modules would be worse than
    this tiny, obviously-in-sync duplication."""
    for _tier, filter_config in registry.all_filters():
        if filter_config.name == name:
            return filter_config
    return None


def run_case(case: BenchmarkCase, benchmarks_dir: Path = BENCHMARKS_DIR) -> BenchmarkResult:
    """Run one benchmark case through the real, unmodified FilterRegistry.

    `.docx`/`.pdf` sample files (QB-007E4) take a second path, mirroring
    `quor/adapters/claude_read.py`'s own DOCX/PDF branch: the binary file is
    converted to text via `quor.pipeline.extract.registry.extract()` (the
    same, unmodified extraction framework) instead of being read as raw
    UTF-8 text, and the filter is looked up *by name*
    (`case.expected_filter`) rather than matched against `case.command` —
    a real `.docx`/`.pdf` command string would never match `markdown.toml`'s
    file-path pattern, exactly as in production. `original_tokens` for
    these cases is therefore tokens in the *extracted* Markdown text, not a
    literal raw Read `tool_response` — what a real `tool_response` contains
    for a binary file remains unconfirmed (see backlog.md's QB-007A/E1
    "Limitations" entries), so this is the most honest figure available to
    benchmark against, not a stand-in for that unknown "before" value.
    """
    sample_path = benchmarks_dir / case.sample_file
    registry = FilterRegistry(skip_user=True, skip_project=True)

    if sample_path.suffix.lower() in _EXTRACTION_EXTENSIONS:
        from quor.pipeline.extract.registry import extract

        original = extract(sample_path) or ""
        filter_config = _find_filter_by_name(registry, case.expected_filter)
    else:
        original = sample_path.read_text(encoding="utf-8")
        filter_config = registry.find(case.command)

    matched_filter = filter_config.name if filter_config else None
    filter_correct = matched_filter == case.expected_filter

    t0 = time.perf_counter()
    final = registry.apply(filter_config, original) if filter_config is not None else original
    execution_time_ms = (time.perf_counter() - t0) * 1000

    original_tokens = count_tokens(original)
    final_tokens = count_tokens(final)
    tokens_saved = original_tokens - final_tokens
    compression_pct = (tokens_saved / original_tokens * 100) if original_tokens else 0.0

    missing = tuple(p for p in case.must_contain if p not in final)
    min_reduction_met = compression_pct >= case.min_reduction_pct

    # Read-only: mirrors the first half of dispatcher.py's _apply_tee() check
    # (content changed?) without ever calling write_tee() — a benchmark run
    # must never write to the real tee cache directory.
    tee_would_fire = content_hash(final) != content_hash(original)

    return BenchmarkResult(
        id=case.id,
        category=case.category,
        ecosystem=case.ecosystem,
        command=case.command,
        matched_filter=matched_filter,
        filter_correct=filter_correct,
        original_tokens=original_tokens,
        final_tokens=final_tokens,
        tokens_saved=tokens_saved,
        compression_pct=compression_pct,
        execution_time_ms=execution_time_ms,
        tee_would_fire=tee_would_fire,
        missing_patterns=missing,
        min_reduction_met=min_reduction_met,
    )


def run_all(
    manifest_path: Path = DEFAULT_MANIFEST, benchmarks_dir: Path = BENCHMARKS_DIR
) -> list[BenchmarkResult]:
    cases = load_manifest(manifest_path)
    return [run_case(c, benchmarks_dir) for c in cases]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _group_by(results: list[BenchmarkResult], key: Any) -> dict[str, dict[str, Any]]:
    """Generic aggregation over an arbitrary, manifest-declared string field
    (`category`, `ecosystem`, or any future grouping — this function has no
    knowledge of what values exist). `key` is a callable taking a
    BenchmarkResult and returning the grouping value for it."""
    grouped: dict[str, dict[str, Any]] = {}
    for group_value in sorted({key(r) for r in results}):
        group_results = [r for r in results if key(r) == group_value]
        group_original = sum(r.original_tokens for r in group_results)
        group_final = sum(r.final_tokens for r in group_results)
        group_saved = group_original - group_final
        grouped[group_value] = {
            "cases": len(group_results),
            "original_tokens": group_original,
            "final_tokens": group_final,
            "tokens_saved": group_saved,
            "compression_pct": round(
                (group_saved / group_original * 100) if group_original else 0.0, 2
            ),
            "avg_execution_time_ms": round(
                statistics.mean(r.execution_time_ms for r in group_results), 3
            ),
        }
    return grouped


def aggregate(results: list[BenchmarkResult], *, top_n: int = 3) -> AggregateSummary:
    total_original = sum(r.original_tokens for r in results)
    total_final = sum(r.final_tokens for r in results)
    total_saved = total_original - total_final
    overall_pct = (total_saved / total_original * 100) if total_original else 0.0
    total_time = sum(r.execution_time_ms for r in results)

    per_category = _group_by(results, key=lambda r: r.category)
    per_ecosystem = _group_by(results, key=lambda r: r.ecosystem)

    ranked = sorted(results, key=lambda r: r.compression_pct, reverse=True)
    best = ranked[:top_n]
    worst = list(reversed(ranked[-top_n:])) if len(ranked) >= top_n else list(reversed(ranked))
    correctness_failures = [r for r in results if not r.correctness_ok]

    return AggregateSummary(
        total_cases=len(results),
        total_original_tokens=total_original,
        total_final_tokens=total_final,
        total_tokens_saved=total_saved,
        overall_compression_pct=overall_pct,
        total_execution_time_ms=total_time,
        per_category=per_category,
        per_ecosystem=per_ecosystem,
        best_performers=best,
        worst_performers=worst,
        correctness_failures=correctness_failures,
    )


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------


def load_baseline(baseline_path: Path) -> dict[str, dict[str, Any]]:
    """Return {case_id: result_dict}, or {} if no baseline exists yet."""
    if not baseline_path.exists():
        return {}
    import orjson

    data = orjson.loads(baseline_path.read_bytes())
    return {entry["id"]: entry for entry in data.get("results", [])}


def compare_to_baseline(
    results: list[BenchmarkResult],
    baseline: dict[str, dict[str, Any]],
    *,
    threshold_pp: float = DEFAULT_REGRESSION_THRESHOLD_PP,
) -> list[ComparisonEntry]:
    comparisons = []
    for r in results:
        base = baseline.get(r.id)
        if base is None:
            comparisons.append(
                ComparisonEntry(
                    id=r.id,
                    status="new",
                    baseline_compression_pct=None,
                    current_compression_pct=r.compression_pct,
                    delta_pp=None,
                )
            )
            continue

        base_pct = float(base["compression_pct"])
        delta = r.compression_pct - base_pct
        if delta < -threshold_pp:
            status = "regression"
        elif delta > threshold_pp:
            status = "improvement"
        else:
            status = "unchanged"

        comparisons.append(
            ComparisonEntry(
                id=r.id,
                status=status,
                baseline_compression_pct=base_pct,
                current_compression_pct=r.compression_pct,
                delta_pp=delta,
            )
        )
    return comparisons


def save_baseline(results: list[BenchmarkResult], baseline_path: Path) -> None:
    import orjson

    payload = {"results": [r.to_dict() for r in results]}
    baseline_path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
