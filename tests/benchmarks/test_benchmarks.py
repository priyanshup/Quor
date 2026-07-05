"""Pytest integration for the compression benchmark suite (QB-011).

This is the automatic half of the validation gate: it runs on every
`pytest tests/` invocation (including CI) and fails the build if a sample
regresses against the committed baseline, drops required content, matches
the wrong filter, or falls below its own min_reduction_pct floor.

Baseline regressions here mean an actual compression change happened, not a
flaky measurement — execution_time_ms is deliberately excluded from every
gate in this file (see benchmark_runner.py's module docstring) because
wall-clock timing is noisy across machines/CI runners and would make this
suite flaky if used as a pass/fail signal.

To update the baseline after an intentional compression change, run:
    python -m tests.benchmarks.run_benchmarks --update-baseline
and commit the resulting tests/benchmarks/baseline.json. See README.md.
"""

from __future__ import annotations

import pytest

from tests.benchmarks.benchmark_runner import (
    DEFAULT_BASELINE,
    DEFAULT_REGRESSION_THRESHOLD_PP,
    compare_to_baseline,
    load_baseline,
    load_manifest,
    run_case,
)

_CASES = load_manifest()
_RESULTS = {case.id: run_case(case) for case in _CASES}


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.id)
class TestBenchmarkCorrectness:
    """Correctness is checked per-case, independent of compression ratio —
    a violation here is always fatal, regardless of tokens saved."""

    def test_matched_expected_filter(self, case) -> None:
        result = _RESULTS[case.id]
        assert result.filter_correct, (
            f"{case.id}: expected filter {case.expected_filter!r}, "
            f"got {result.matched_filter!r}"
        )

    def test_required_content_survives(self, case) -> None:
        result = _RESULTS[case.id]
        assert not result.missing_patterns, (
            f"{case.id}: required content missing from filtered output: "
            f"{list(result.missing_patterns)}"
        )

    def test_meets_min_reduction_floor(self, case) -> None:
        result = _RESULTS[case.id]
        assert result.min_reduction_met, (
            f"{case.id}: compression {result.compression_pct:.1f}% is below "
            f"its configured floor of {case.min_reduction_pct}%"
        )


def test_no_regression_against_baseline() -> None:
    baseline = load_baseline(DEFAULT_BASELINE)
    if not baseline:
        pytest.skip(
            "No baseline at tests/benchmarks/baseline.json yet — run "
            "`python -m tests.benchmarks.run_benchmarks --update-baseline` once."
        )
    results = list(_RESULTS.values())
    comparisons = compare_to_baseline(
        results, baseline, threshold_pp=DEFAULT_REGRESSION_THRESHOLD_PP
    )
    regressions = [c for c in comparisons if c.status == "regression"]
    assert not regressions, (
        "Compression regressed beyond "
        f"{DEFAULT_REGRESSION_THRESHOLD_PP}pp for: "
        + ", ".join(
            f"{c.id} ({c.baseline_compression_pct:.1f}% -> {c.current_compression_pct:.1f}%)"
            for c in regressions
        )
    )
