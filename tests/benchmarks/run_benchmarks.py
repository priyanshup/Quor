"""CLI entrypoint for the Quor compression benchmark suite (QB-011).

Usage (run from the repository root):

    python -m tests.benchmarks.run_benchmarks
    python -m tests.benchmarks.run_benchmarks --update-baseline
    python -m tests.benchmarks.run_benchmarks --regression-threshold 5.0
    python -m tests.benchmarks.run_benchmarks --output-dir some/dir --format json

See tests/benchmarks/README.md for the full guide (adding cases, updating
the baseline, interpreting failures).

Exit codes:
    0 — all correctness checks passed, every case cleared its min_reduction_pct
        floor, and no baseline regression exceeded the threshold.
    1 — a correctness failure (wrong filter matched, or a must_contain
        pattern went missing), a min_reduction_pct floor violation, or a
        baseline regression beyond the configured threshold.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tests.benchmarks.benchmark_runner import (
    BENCHMARKS_DIR,
    DEFAULT_BASELINE,
    DEFAULT_MANIFEST,
    DEFAULT_REGRESSION_THRESHOLD_PP,
    aggregate,
    compare_to_baseline,
    load_baseline,
    run_all,
    save_baseline,
)
from tests.benchmarks.report import write_json_report, write_markdown_report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    results = run_all(manifest_path=args.manifest, benchmarks_dir=BENCHMARKS_DIR)
    summary = aggregate(results)

    correctness_failed = bool(summary.correctness_failures)
    if correctness_failed:
        print("CORRECTNESS FAILURES:")
        for r in summary.correctness_failures:
            if not r.filter_correct:
                print(f"  [{r.id}] expected a different filter, got {r.matched_filter!r}")
            if r.missing_patterns:
                print(f"  [{r.id}] missing required content: {list(r.missing_patterns)}")
        print()

    floor_violations = [r for r in results if not r.min_reduction_met]
    floor_failed = bool(floor_violations)
    if floor_failed:
        print("MIN-REDUCTION FLOOR VIOLATIONS:")
        for r in floor_violations:
            print(f"  [{r.id}] {r.compression_pct:.1f}% is below its configured floor")
        print()

    comparisons = None
    regression_failed = False
    if args.update_baseline:
        if correctness_failed or floor_failed:
            print("Refusing to update baseline: correctness/floor failures present.")
            return 1
        save_baseline(results, args.baseline)
        print(f"Baseline updated: {args.baseline}")
    elif not args.no_compare:
        baseline = load_baseline(args.baseline)
        if baseline:
            comparisons = compare_to_baseline(
                results, baseline, threshold_pp=args.regression_threshold
            )
            regressions = [c for c in comparisons if c.status == "regression"]
            if regressions:
                regression_failed = True
                print(f"REGRESSIONS (threshold {args.regression_threshold:+.1f}pp):")
                for c in regressions:
                    print(f"  [{c.id}] {c.baseline_compression_pct:.1f}% -> {c.current_compression_pct:.1f}% ({c.delta_pp:+.1f}pp)")
                print()
        else:
            print(f"No baseline found at {args.baseline} — run with --update-baseline to create one.")

    print(
        f"{summary.total_cases} cases | "
        f"{summary.total_tokens_saved} tokens saved "
        f"({summary.overall_compression_pct:.1f}% overall) | "
        f"{summary.total_execution_time_ms:.2f} ms total"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.format in ("json", "both"):
        json_path = args.output_dir / "benchmark-results.json"
        write_json_report(results, summary, comparisons, json_path)
        print(f"JSON report:     {json_path}")
    if args.format in ("markdown", "both"):
        md_path = args.output_dir / "benchmark-report.md"
        write_markdown_report(results, summary, comparisons, md_path)
        print(f"Markdown report: {md_path}")

    if correctness_failed or floor_failed or regression_failed:
        return 1
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Quor compression benchmark suite.")
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST, help="Path to manifest.toml"
    )
    parser.add_argument(
        "--baseline", type=Path, default=DEFAULT_BASELINE, help="Path to baseline.json"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Save this run's results as the new baseline instead of comparing against it.",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Skip baseline comparison entirely (metrics/reports only).",
    )
    parser.add_argument(
        "--regression-threshold",
        type=float,
        default=DEFAULT_REGRESSION_THRESHOLD_PP,
        help="Percentage-point drop in compression that counts as a regression "
        f"(default: {DEFAULT_REGRESSION_THRESHOLD_PP}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BENCHMARKS_DIR / "results",
        help="Directory to write benchmark-results.json / benchmark-report.md into.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown", "both"),
        default="both",
        help="Which report format(s) to write.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
