"""CLI entrypoint for the Quor compression benchmark suite (QB-011).

Usage (run from the repository root):

    python -m tests.benchmarks.run_benchmarks
    python -m tests.benchmarks.run_benchmarks --update-baseline
    python -m tests.benchmarks.run_benchmarks --regression-threshold 5.0
    python -m tests.benchmarks.run_benchmarks --output-dir some/dir --format json
    python -m tests.benchmarks.run_benchmarks --analytics
    python -m tests.benchmarks.run_benchmarks --history

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

from quor import __version__ as quor_version
from quor.analytics.effectiveness import classify
from tests.benchmarks.analytics_report import collect_stage_stats, render_analytics_report
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
from tests.benchmarks.history import (
    DEFAULT_HISTORY_PATH,
    append_entry,
    build_entry,
    detect_regression,
    render_history_table,
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

    if args.analytics:
        analytics_text = render_analytics_report(results, summary)
        print()
        print(analytics_text, end="")
        analytics_path = args.output_dir / "analytics-report.txt"
        analytics_path.write_text(analytics_text, encoding="utf-8")
        print(f"Analytics report: {analytics_path}")

    if args.history:
        stage_stats = collect_stage_stats(results)
        ratings = classify(stage_stats)
        total_saved = sum(s.tokens_saved for s in stage_stats.values())
        entry = build_entry(
            version=quor_version,
            total_cases=summary.total_cases,
            overall_compression_pct=summary.overall_compression_pct,
            total_tokens_saved=summary.total_tokens_saved,
            per_stage_contribution_pct={
                r.stage_type: (
                    stage_stats[r.stage_type].tokens_saved / total_saved * 100
                    if total_saved
                    else 0.0
                )
                for r in ratings
            },
            per_ecosystem_compression_pct={
                name: float(stats["compression_pct"]) for name, stats in summary.per_ecosystem.items()
            },
        )
        entries = append_entry(entry, args.history_path)
        print()
        print(f"History updated: {args.history_path}")
        print(render_history_table(entries), end="")
        if len(entries) >= 2:
            _, message = detect_regression(entries, threshold_pp=args.regression_threshold)
            print(message)

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
    parser.add_argument(
        "--analytics",
        action="store_true",
        help="Also print/write the QB-039 analytics report (stage contribution, "
        "language contribution, hardest files, effectiveness rating).",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Also append this run to the benchmark history file (keyed by "
        "quor.__version__) and print the version-over-version comparison table.",
    )
    parser.add_argument(
        "--history-path",
        type=Path,
        default=DEFAULT_HISTORY_PATH,
        help="Path to history.json (only used with --history).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
