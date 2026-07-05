"""JSON + Markdown report generation for the compression benchmark suite (QB-011).

Presentation only — every number here comes from BenchmarkResult/
AggregateSummary/ComparisonEntry (benchmark_runner.py). This module never
computes a metric, it only formats ones that already exist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

from tests.benchmarks.benchmark_runner import (
    AggregateSummary,
    BenchmarkResult,
    ComparisonEntry,
)


def write_json_report(
    results: list[BenchmarkResult],
    summary: AggregateSummary,
    comparisons: list[ComparisonEntry] | None,
    output_path: Path,
) -> None:
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # noqa: UP017
        "results": [r.to_dict() for r in results],
        "summary": {
            "total_cases": summary.total_cases,
            "total_original_tokens": summary.total_original_tokens,
            "total_final_tokens": summary.total_final_tokens,
            "total_tokens_saved": summary.total_tokens_saved,
            "overall_compression_pct": round(summary.overall_compression_pct, 2),
            "total_execution_time_ms": round(summary.total_execution_time_ms, 3),
            "per_category": summary.per_category,
            "per_ecosystem": summary.per_ecosystem,
            "best_performers": [r.id for r in summary.best_performers],
            "worst_performers": [r.id for r in summary.worst_performers],
            "correctness_failures": [r.id for r in summary.correctness_failures],
        },
    }
    if comparisons is not None:
        payload["baseline_comparison"] = [
            {
                "id": c.id,
                "status": c.status,
                "baseline_compression_pct": c.baseline_compression_pct,
                "current_compression_pct": round(c.current_compression_pct, 2),
                "delta_pp": round(c.delta_pp, 2) if c.delta_pp is not None else None,
            }
            for c in comparisons
        ]
    output_path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))


def _render_grouped_table(lines: list[str], *, heading: str, header: str, grouped: dict[str, dict[str, Any]]) -> None:
    """Render one "grouped by X" table. `grouped` is whatever _group_by()
    produced in benchmark_runner.py — this function has no idea whether the
    keys are categories, ecosystems, or any future grouping; it just
    formats what it's given."""
    lines.append(f"## {heading}")
    lines.append("")
    lines.append(f"| {header} | Cases | Tokens before | Tokens after | Saved | Compression % | Avg time (ms) |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, stats in sorted(grouped.items()):
        lines.append(
            f"| {name} | {stats['cases']} | {stats['original_tokens']} | "
            f"{stats['final_tokens']} | {stats['tokens_saved']} | "
            f"{stats['compression_pct']:.1f}% | {stats['avg_execution_time_ms']:.2f} |"
        )
    lines.append("")


def write_markdown_report(
    results: list[BenchmarkResult],
    summary: AggregateSummary,
    comparisons: list[ComparisonEntry] | None,
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Quor Compression Benchmark Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")  # noqa: UP017
    lines.append("")

    lines.append("## Overall totals")
    lines.append("")
    lines.append(f"- Cases run: {summary.total_cases}")
    lines.append(f"- Tokens before: {summary.total_original_tokens}")
    lines.append(f"- Tokens after: {summary.total_final_tokens}")
    lines.append(f"- Tokens saved: {summary.total_tokens_saved}")
    lines.append(f"- Overall compression: {summary.overall_compression_pct:.1f}%")
    lines.append(f"- Total execution time: {summary.total_execution_time_ms:.2f} ms")
    if summary.correctness_failures:
        ids = ", ".join(r.id for r in summary.correctness_failures)
        lines.append(f"- **Correctness failures: {ids}**")
    lines.append("")

    _render_grouped_table(
        lines, heading="Per-ecosystem summary", header="Ecosystem", grouped=summary.per_ecosystem
    )
    _render_grouped_table(
        lines, heading="Per-filter summary", header="Category", grouped=summary.per_category
    )

    lines.append("## Per-sample results")
    lines.append("")
    lines.append("| ID | Filter | Original | Final | Saved | Compression % | Time (ms) | Tee | Correct |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        correct_mark = "✓" if r.correctness_ok else "✗"
        tee_mark = "yes" if r.tee_would_fire else "no"
        lines.append(
            f"| {r.id} | {r.matched_filter or '(none)'} | {r.original_tokens} | "
            f"{r.final_tokens} | {r.tokens_saved} | {r.compression_pct:.1f}% | "
            f"{r.execution_time_ms:.2f} | {tee_mark} | {correct_mark} |"
        )
    lines.append("")

    lines.append("## Best performers")
    lines.append("")
    for r in summary.best_performers:
        lines.append(f"- `{r.id}` — {r.compression_pct:.1f}% compression ({r.tokens_saved} tokens saved)")
    lines.append("")

    lines.append("## Worst performers")
    lines.append("")
    for r in summary.worst_performers:
        lines.append(f"- `{r.id}` — {r.compression_pct:.1f}% compression ({r.tokens_saved} tokens saved)")
    lines.append("")

    if comparisons is not None:
        lines.append("## Baseline comparison")
        lines.append("")
        lines.append("| ID | Status | Baseline % | Current % | Delta (pp) |")
        lines.append("|---|---|---|---|---|")
        for c in comparisons:
            baseline_str = f"{c.baseline_compression_pct:.1f}%" if c.baseline_compression_pct is not None else "—"
            delta_str = f"{c.delta_pp:+.1f}" if c.delta_pp is not None else "—"
            lines.append(
                f"| {c.id} | {c.status} | {baseline_str} | "
                f"{c.current_compression_pct:.1f}% | {delta_str} |"
            )
        lines.append("")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
