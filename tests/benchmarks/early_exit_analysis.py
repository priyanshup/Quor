"""QB-036: early-exit correctness + performance analysis across the full
compression benchmark corpus.

A second, deliberately separate script from the rest of this suite — run it
directly (`python -m tests.benchmarks.early_exit_analysis`), it is not wired
into `test_benchmarks.py`'s pytest gate. Mirrors `ast_timing_analysis.py`'s
own precedent (QB-005E): this measures an *operational* property (does early
exit ever change output; how much time does it save) rather than
regression-tracked compression quality, which `manifest.toml`/`baseline.json`
already own.

Two things are checked, for every case in `manifest.toml`, using each case's
real sample file and real matched filter (same routing `benchmark_runner.py`
already uses — this script imports and reuses `run_case`'s own sample-loading
logic rather than duplicating it):

1. Correctness: `FilterRegistry.apply()` (early exit on, the default) vs. the
   same pipeline forced to `early_exit=False` — the two renders must be
   byte-for-byte identical for every single case. Any mismatch is reported as
   a hard failure (exit code 1), not a warning.
2. Performance: wall-clock time for both variants, averaged over several
   repetitions per case (single-run timing is too noisy at this scale to
   trust, consistent with this whole suite's "execution_time_ms is never a
   hard gate" philosophy). Reports how many cases actually triggered early
   exit (mid-pipeline, not just "the last stage never had anything to do
   anyway") and the aggregate timing delta.

Isolated from production code the same way `benchmark_runner.py` and
`ast_timing_analysis.py` already are: only calls `quor.filters.registry.
FilterRegistry`'s existing public surface (`apply()`, `find()`,
`_run_pipeline()` — the same module-private method `tests/unit/
test_early_exit.py` already uses for this exact comparison) and
`quor.pipeline.extract.registry.extract()`. Nothing in `quor/` was touched to
support this script.
"""

from __future__ import annotations

import statistics
import time

from quor.filters.registry import FilterRegistry
from quor.pipeline.extract.registry import extract

from .benchmark_runner import BENCHMARKS_DIR, DEFAULT_MANIFEST, BenchmarkCase, load_manifest

_EXTRACTION_EXTENSIONS = frozenset({".docx", ".pdf"})
_REPEATS = 25  # per case, per variant — averaged for stability


def _find_filter_by_name(registry: FilterRegistry, name: str) -> object:
    for _tier, filter_config in registry.all_filters():
        if filter_config.name == name:
            return filter_config
    return None


def _load_original(case: BenchmarkCase, registry: FilterRegistry) -> tuple[str, object]:
    """Mirrors benchmark_runner.run_case()'s own sample-loading/routing
    exactly (DOCX/PDF extraction + by-name lookup vs. plain text + command
    match) — duplicated rather than imported since run_case() bundles this
    together with timing/scoring this script does differently."""
    sample_path = BENCHMARKS_DIR / case.sample_file
    if sample_path.suffix.lower() in _EXTRACTION_EXTENSIONS:
        original = extract(sample_path) or ""
        filter_config = _find_filter_by_name(registry, case.expected_filter)
    else:
        original = sample_path.read_text(encoding="utf-8")
        filter_config = registry.find(case.command)
    return original, filter_config


def _apply_with_flag(registry: FilterRegistry, filter_config: object, content: str, *, early_exit: bool) -> str:
    """Reimplements FilterRegistry.apply()'s abort_unless/abort_if/on_empty
    logic (unmodified by QB-036) so early_exit is the only variable —
    identical technique to tests/unit/test_early_exit.py's own helper."""
    if filter_config.abort_unless and not any(  # type: ignore[attr-defined]
        s in content for s in filter_config.abort_unless  # type: ignore[attr-defined]
    ):
        return content
    if filter_config.abort_if and any(  # type: ignore[attr-defined]
        s in content for s in filter_config.abort_if  # type: ignore[attr-defined]
    ):
        return content
    rendered = registry._run_pipeline(
        filter_config, content, early_exit=early_exit
    ).mask.render()
    if not rendered.strip() and filter_config.on_empty:  # type: ignore[attr-defined]
        return filter_config.on_empty  # type: ignore[attr-defined]
    return rendered


def _early_exit_actually_fired(registry: FilterRegistry, filter_config: object, content: str) -> bool:
    result = registry._run_pipeline(filter_config, content, early_exit=True)
    return any("early exit" in r.skip_reason for r in result.stage_results)


def main() -> int:
    registry = FilterRegistry(skip_user=True, skip_project=True)
    cases = load_manifest(DEFAULT_MANIFEST)

    mismatches: list[str] = []
    fired_cases: list[str] = []
    time_with_ms: list[float] = []
    time_without_ms: list[float] = []
    per_case_rows: list[tuple[str, float, float, bool]] = []

    for case in cases:
        original, filter_config = _load_original(case, registry)
        if filter_config is None:
            continue  # unrouted case — nothing for early exit to affect either way

        with_opt = _apply_with_flag(registry, filter_config, original, early_exit=True)
        without_opt = _apply_with_flag(registry, filter_config, original, early_exit=False)
        if with_opt != without_opt:
            mismatches.append(case.id)
            continue

        fired = _early_exit_actually_fired(registry, filter_config, original)
        if fired:
            fired_cases.append(case.id)

        with_times = []
        without_times = []
        for _ in range(_REPEATS):
            t0 = time.perf_counter()
            _apply_with_flag(registry, filter_config, original, early_exit=True)
            with_times.append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            _apply_with_flag(registry, filter_config, original, early_exit=False)
            without_times.append((time.perf_counter() - t0) * 1000)

        median_with = statistics.median(with_times)
        median_without = statistics.median(without_times)
        time_with_ms.append(median_with)
        time_without_ms.append(median_without)
        per_case_rows.append((case.id, median_with, median_without, fired))

    print(f"{len(cases)} cases loaded, {len(per_case_rows)} routed and measured\n")

    if mismatches:
        print("CORRECTNESS FAILURE — early exit changed output for:")
        for cid in mismatches:
            print(f"  - {cid}")
        return 1

    print(f"Correctness: {len(per_case_rows)}/{len(per_case_rows)} cases byte-for-byte identical "
          "with early_exit on vs. off.\n")

    print(f"Early exit actually fired (skipped >=1 stage) in {len(fired_cases)}/{len(per_case_rows)} cases:")
    for cid in fired_cases:
        print(f"  - {cid}")

    total_with = sum(time_with_ms)
    total_without = sum(time_without_ms)
    delta = total_without - total_with
    delta_pct = (delta / total_without * 100) if total_without else 0.0

    print(f"\nAggregate timing (median of {_REPEATS} runs per case per variant):")
    print(f"  early_exit=True  total: {total_with:.3f} ms")
    print(f"  early_exit=False total: {total_without:.3f} ms")
    print(f"  delta: {delta:+.3f} ms ({delta_pct:+.2f}%)")

    if fired_cases:
        print("\nPer-case timing where early exit fired:")
        fired_set = set(fired_cases)
        for cid, wm, wom, _fired in per_case_rows:
            if cid in fired_set:
                case_delta_pct = ((wom - wm) / wom * 100) if wom else 0.0
                print(f"  {cid}: with={wm:.4f}ms without={wom:.4f}ms delta={case_delta_pct:+.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
