"""Machine-readable `quor gain` export — JSON/CSV serializers (QB-131).

Presentation-only, like `quor/cli/gain_presentation.py` and
`quor/cli/format_utils.py`: this module never computes a metric, it only
maps already-computed `GainReport`/`ToolUsage`/`FileUsage` values (see
`quor/tracking/db.py`) onto a machine-readable shape. Numbers are written
as real `int`/`float`, never pre-formatted strings (`format_count()`'s "20.1k"
style is for the Rich table only) — a machine reader should never have to
re-parse a human-facing abbreviation.

`by_tool`/`by_file` are `None` when that breakdown wasn't requested (the key
is omitted from the payload entirely) versus `()` when it was requested but
the window has no matching rows (the key is present as an empty list/
header-only CSV section) — the same "requested-but-empty is not the same as
never-asked" distinction `query_gain_by_tool()`/`query_gain_by_file()`
themselves preserve by returning `()` rather than raising.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import orjson

from quor.tracking.db import FileUsage, GainReport, ToolUsage


def build_gain_payload(
    report: GainReport,
    *,
    by_tool: tuple[ToolUsage, ...] | None = None,
    by_file: tuple[FileUsage, ...] | None = None,
) -> dict[str, Any]:
    """Assemble the machine-readable payload for one `quor gain` run.

    `summary` mirrors `GainReport` field-for-field (plus `percentage_saved`,
    which `GainReport` doesn't store — `quor/cli/commands/gain.py`'s own
    Rich rendering computes the same `tokens_saved / tokens_before` fraction
    inline for the headline; this is that same identity, not a new metric).
    """
    percentage_saved = (
        round(report.tokens_saved / report.tokens_before * 100, 2) if report.tokens_before else 0.0
    )
    payload: dict[str, Any] = {
        "summary": {
            "days": report.days,
            "total_operations": report.total_invocations,
            "tokens_before": report.tokens_before,
            "tokens_after": report.tokens_after,
            "tokens_saved": report.tokens_saved,
            "percentage_saved": percentage_saved,
            "gross_savings": report.gross_savings,
            "gross_overhead": report.gross_overhead,
            "negative_row_count": report.negative_row_count,
            "passthrough_count": report.passthrough_count,
            "filter_hit_rate_pct": round(report.filter_hit_rate * 100, 2),
            "read_hook_invocations": report.read_hook_invocations,
        }
    }
    if by_tool is not None:
        payload["by_tool"] = [
            {
                "tool": t.tool,
                "operations": t.operations,
                "tokens_before": t.tokens_before,
                "tokens_after": t.tokens_after,
                "tokens_saved": t.tokens_saved,
                "compression_pct": round(t.compression_pct, 2),
            }
            for t in by_tool
        ]
    if by_file is not None:
        payload["by_file"] = [
            {
                "file_path": f.file_path,
                "operations": f.operations,
                "tokens_before": f.tokens_before,
                "tokens_after": f.tokens_after,
                "tokens_saved": f.tokens_saved,
                "compression_pct": round(f.compression_pct, 2),
            }
            for f in by_file
        ]
    return payload


def render_gain_json(payload: dict[str, Any]) -> str:
    """Serialize `payload` as indented JSON. `by_tool: []`/`by_file: []`
    (an empty but present list — see module docstring) round-trip exactly
    as `[]`, never as `null` or an omitted key, since `build_gain_payload()`
    only ever puts a real (possibly empty) list at those keys."""
    return orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()


_SUMMARY_FIELDS = (
    "days",
    "total_operations",
    "tokens_before",
    "tokens_after",
    "tokens_saved",
    "percentage_saved",
    "gross_savings",
    "gross_overhead",
    "negative_row_count",
    "passthrough_count",
    "filter_hit_rate_pct",
    "read_hook_invocations",
)

_BY_TOOL_FIELDS = (
    "tool",
    "operations",
    "tokens_before",
    "tokens_after",
    "tokens_saved",
    "compression_pct",
)

_BY_FILE_FIELDS = (
    "file_path",
    "operations",
    "tokens_before",
    "tokens_after",
    "tokens_saved",
    "compression_pct",
)


def render_gain_csv(payload: dict[str, Any]) -> str:
    """Serialize `payload` as CSV.

    A `GainReport` isn't naturally one flat table — it's a single summary
    plus zero or more breakdowns — so this writes one section per requested
    piece, each its own header row followed by its data rows, blank-line
    separated: `summary` (always, exactly one data row), then `by_tool`/
    `by_file` (only when their key is present in `payload`). A breakdown
    key present with zero rows still writes its header — "header-only",
    never an omitted section — matching `build_gain_payload()`'s own
    present-but-empty-list convention for JSON.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")

    summary = payload["summary"]
    writer.writerow(_SUMMARY_FIELDS)
    writer.writerow([summary[field] for field in _SUMMARY_FIELDS])

    if "by_tool" in payload:
        buf.write("\n")
        writer.writerow(_BY_TOOL_FIELDS)
        for row in payload["by_tool"]:
            writer.writerow([row[field] for field in _BY_TOOL_FIELDS])

    if "by_file" in payload:
        buf.write("\n")
        writer.writerow(_BY_FILE_FIELDS)
        for row in payload["by_file"]:
            writer.writerow([row[field] for field in _BY_FILE_FIELDS])

    return buf.getvalue()
