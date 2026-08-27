"""Unit tests for QB-131: `quor gain --format`/`--by-tool`/`--by-file`.

Covers three layers:
  - query_gain_by_tool()/query_gain_by_file() (quor/tracking/db.py) —
    aggregation correctness against a real SQLite file, mirroring
    tests/unit/test_tracking.py's own TestQueryGain conventions.
  - build_gain_payload()/render_gain_json()/render_gain_csv()
    (quor/cli/gain_exporter.py) — pure serialization, no I/O.
  - `quor gain` CLI wiring (quor/cli/commands/gain.py) — format
    validation and end-to-end output via CliRunner, mirroring
    tests/unit/test_cli.py's own TestGain conventions.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from quor.cli.gain_exporter import build_gain_payload, render_gain_csv, render_gain_json
from quor.cli.main import app
from quor.tracking.db import (
    FileUsage,
    GainReport,
    ToolUsage,
    query_gain_by_file,
    query_gain_by_tool,
)

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed(db_path: Path, records: list[dict]) -> None:
    """Write rows directly against schema.sql, bypassing TrackingDB's
    background thread — same approach test_tracking.py's own
    `_seed_invocations()` uses, kept local to this file rather than
    importing a private helper across test modules."""
    schema_sql = (
        Path(__file__).parent.parent.parent / "quor" / "tracking" / "schema.sql"
    ).read_text(encoding="utf-8")
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(schema_sql)
        for r in records:
            conn.execute(
                """INSERT INTO invocations
                   (command, project_path, original_tokens, final_tokens,
                    filter_name, was_passthrough, duration_ms, recorded_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r.get("command", "git status"),
                    r.get("project_path", "/proj"),
                    r.get("original_tokens", 100),
                    r.get("final_tokens", 20),
                    r.get("filter_name", "git"),
                    r.get("was_passthrough", 0),
                    r.get("duration_ms", 10.0),
                    r.get("recorded_at", datetime.now(UTC).isoformat(timespec="seconds")),
                    1,
                ),
            )
        conn.commit()


def _report(**overrides: object) -> GainReport:
    defaults: dict[str, object] = {
        "total_invocations": 10,
        "tokens_saved": 80,
        "tokens_before": 100,
        "tokens_after": 20,
        "eligible_before": 100,
        "gross_savings": 80,
        "gross_overhead": 0,
        "negative_row_count": 0,
        "passthrough_count": 0,
        "filter_hit_rate": 1.0,
        "top_filters": [],
        "days": 30,
        "read_hook_invocations": 0,
    }
    defaults.update(overrides)
    return GainReport(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# query_gain_by_tool — aggregation correctness
# ---------------------------------------------------------------------------


class TestQueryGainByTool:
    def test_empty_database_returns_empty_tuple(self, tmp_path: Path) -> None:
        assert query_gain_by_tool(tmp_path / "missing.db", tmp_path / "proj") == ()

    def test_splits_by_literal_command_prefix(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        project = tmp_path / "proj"
        _seed(
            db_path,
            [
                {
                    "command": "git status",
                    "project_path": project.as_posix(),
                    "original_tokens": 100,
                    "final_tokens": 20,
                    "filter_name": "git-status",
                },
                {
                    "command": "Read: app.py",
                    "project_path": project.as_posix(),
                    "original_tokens": 400,
                    "final_tokens": 100,
                    "filter_name": "cat-python",
                },
                {
                    "command": "MCP compress_context",
                    "project_path": project.as_posix(),
                    "original_tokens": 500,
                    "final_tokens": 100,
                    "filter_name": "git-diff",
                },
                {
                    "command": "MCP compress_context: focal_file=foo.py",
                    "project_path": project.as_posix(),
                    "original_tokens": 300,
                    "final_tokens": 50,
                    "filter_name": "cat-python",
                },
                {
                    "command": "MCP get_repo_context: foo.py",
                    "project_path": project.as_posix(),
                    "original_tokens": 50,
                    "final_tokens": 50,
                    "filter_name": "mcp-repo-context",
                },
            ],
        )

        by_tool = {u.tool: u for u in query_gain_by_tool(db_path, project, days=30)}

        assert set(by_tool) == {"cli", "compress_context", "get_repo_context"}

        cli = by_tool["cli"]
        assert cli.operations == 2
        assert cli.tokens_before == 500
        assert cli.tokens_after == 120
        assert cli.tokens_saved == 380

        cc = by_tool["compress_context"]
        assert cc.operations == 2
        assert cc.tokens_before == 800
        assert cc.tokens_after == 150
        assert cc.tokens_saved == 650

        grc = by_tool["get_repo_context"]
        assert grc.operations == 1
        assert grc.tokens_saved == 0
        assert grc.compression_pct == 0.0

    def test_get_repo_context_not_excluded_despite_synthesis_label(self, tmp_path: Path) -> None:
        """QB-092/QB-105's SYNTHESIS_FILTER_LABELS exclusion (query_gain's
        own headline aggregate) must NOT apply here — it would zero out the
        entire get_repo_context bucket, defeating the point of --by-tool."""
        db_path = tmp_path / "quor.db"
        project = tmp_path / "proj"
        _seed(
            db_path,
            [
                {
                    "command": "MCP get_repo_context: (no args)",
                    "project_path": project.as_posix(),
                    "original_tokens": 42,
                    "final_tokens": 42,
                    "filter_name": "mcp-repo-context",
                },
            ],
        )
        by_tool = query_gain_by_tool(db_path, project, days=30)
        assert len(by_tool) == 1
        assert by_tool[0].tool == "get_repo_context"
        assert by_tool[0].operations == 1

    def test_only_buckets_with_data_are_returned(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        project = tmp_path / "proj"
        _seed(
            db_path,
            [{"command": "git status", "project_path": project.as_posix()}],
        )
        by_tool = query_gain_by_tool(db_path, project, days=30)
        assert len(by_tool) == 1
        assert by_tool[0].tool == "cli"


# ---------------------------------------------------------------------------
# query_gain_by_file — top-N sorting
# ---------------------------------------------------------------------------


class TestQueryGainByFile:
    def test_empty_database_returns_empty_tuple(self, tmp_path: Path) -> None:
        assert query_gain_by_file(tmp_path / "missing.db", tmp_path / "proj") == ()

    def test_aggregates_and_sorts_by_net_savings_desc(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        project = tmp_path / "proj"
        _seed(
            db_path,
            [
                {
                    "command": "Read: app.py",
                    "project_path": project.as_posix(),
                    "original_tokens": 400,
                    "final_tokens": 100,
                },
                {
                    "command": "Read: app.py",
                    "project_path": project.as_posix(),
                    "original_tokens": 100,
                    "final_tokens": 50,
                },
                {
                    "command": "Read: util.py",
                    "project_path": project.as_posix(),
                    "original_tokens": 200,
                    "final_tokens": 150,
                },
                {
                    "command": "MCP compress_context: focal_file=big.py",
                    "project_path": project.as_posix(),
                    "original_tokens": 1000,
                    "final_tokens": 100,
                },
                {
                    # Bash-dispatched — no identifiable file, must be excluded.
                    "command": "git diff",
                    "project_path": project.as_posix(),
                    "original_tokens": 5000,
                    "final_tokens": 4990,
                },
                {
                    # compress_context with no focal_file — no identifiable file.
                    "command": "MCP compress_context",
                    "project_path": project.as_posix(),
                    "original_tokens": 300,
                    "final_tokens": 290,
                },
            ],
        )

        by_file = query_gain_by_file(db_path, project, days=30)

        assert [f.file_path for f in by_file] == ["big.py", "app.py", "util.py"]
        app_py = next(f for f in by_file if f.file_path == "app.py")
        assert app_py.operations == 2
        assert app_py.tokens_before == 500
        assert app_py.tokens_after == 150
        assert app_py.tokens_saved == 350
        big_py = next(f for f in by_file if f.file_path == "big.py")
        assert big_py.tokens_saved == 900

    def test_limit_truncates_to_top_n(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        project = tmp_path / "proj"
        records = [
            {
                "command": f"Read: file{i}.py",
                "project_path": project.as_posix(),
                "original_tokens": 100 + i,
                "final_tokens": 10,
            }
            for i in range(15)
        ]
        _seed(db_path, records)

        by_file = query_gain_by_file(db_path, project, days=30, limit=10)

        assert len(by_file) == 10
        # Highest original_tokens (file14) saves the most and sorts first.
        assert by_file[0].file_path == "file14.py"
        savings = [f.tokens_saved for f in by_file]
        assert savings == sorted(savings, reverse=True)


# ---------------------------------------------------------------------------
# build_gain_payload / render_gain_json / render_gain_csv
# ---------------------------------------------------------------------------


class TestBuildGainPayload:
    def test_summary_only_when_breakdowns_not_requested(self) -> None:
        payload = build_gain_payload(_report(tokens_saved=80, tokens_before=100))
        assert "by_tool" not in payload
        assert "by_file" not in payload
        assert payload["summary"]["percentage_saved"] == 80.0
        assert payload["summary"]["total_operations"] == 10

    def test_percentage_saved_zero_when_no_tokens_before(self) -> None:
        payload = build_gain_payload(_report(tokens_before=0, tokens_saved=0))
        assert payload["summary"]["percentage_saved"] == 0.0

    def test_empty_breakdown_present_as_empty_list_not_omitted(self) -> None:
        payload = build_gain_payload(_report(), by_tool=(), by_file=())
        assert payload["by_tool"] == []
        assert payload["by_file"] == []

    def test_breakdown_rows_map_to_key_value_dicts(self) -> None:
        by_tool = (
            ToolUsage(
                tool="compress_context",
                operations=5,
                tokens_before=1000,
                tokens_after=200,
                tokens_saved=800,
                compression_pct=80.0,
            ),
        )
        by_file = (
            FileUsage(
                file_path="app.py",
                operations=3,
                tokens_before=500,
                tokens_after=100,
                tokens_saved=400,
                compression_pct=80.0,
            ),
        )
        payload = build_gain_payload(_report(), by_tool=by_tool, by_file=by_file)
        assert payload["by_tool"] == [
            {
                "tool": "compress_context",
                "operations": 5,
                "tokens_before": 1000,
                "tokens_after": 200,
                "tokens_saved": 800,
                "compression_pct": 80.0,
            }
        ]
        assert payload["by_file"][0]["file_path"] == "app.py"
        assert payload["by_file"][0]["tokens_saved"] == 400


class TestRenderGainJson:
    def test_round_trips_as_valid_json(self) -> None:
        payload = build_gain_payload(_report(), by_tool=(), by_file=())
        parsed = json.loads(render_gain_json(payload))
        assert parsed == payload

    def test_numeric_fields_are_real_numbers_not_strings(self) -> None:
        payload = build_gain_payload(_report(tokens_saved=1234, tokens_before=5000))
        parsed = json.loads(render_gain_json(payload))
        assert isinstance(parsed["summary"]["tokens_saved"], int)
        assert parsed["summary"]["tokens_saved"] == 1234


class TestRenderGainCsv:
    def test_summary_section_has_header_and_one_data_row(self) -> None:
        payload = build_gain_payload(_report(tokens_saved=80, tokens_before=100))
        text = render_gain_csv(payload)
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == [
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
        ]
        assert rows[1][4] == "80"  # tokens_saved

    def test_empty_by_tool_renders_header_only_no_exception(self) -> None:
        payload = build_gain_payload(_report(), by_tool=())
        text = render_gain_csv(payload)
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
        header_idx = rows.index(
            ["tool", "operations", "tokens_before", "tokens_after", "tokens_saved", "compression_pct"]
        )
        # Nothing but the header follows for by_tool — the next non-blank
        # section (if any) or EOF comes right after.
        assert header_idx == len(rows) - 1

    def test_by_tool_section_omitted_when_not_requested(self) -> None:
        payload = build_gain_payload(_report())
        text = render_gain_csv(payload)
        assert "tool,operations" not in text

    def test_by_file_rows_present_when_data_given(self) -> None:
        by_file = (
            FileUsage(
                file_path="app.py",
                operations=1,
                tokens_before=100,
                tokens_after=20,
                tokens_saved=80,
                compression_pct=80.0,
            ),
        )
        payload = build_gain_payload(_report(), by_file=by_file)
        text = render_gain_csv(payload)
        rows = list(csv.reader(io.StringIO(text)))
        assert ["app.py", "1", "100", "20", "80", "80.0"] in rows


# ---------------------------------------------------------------------------
# CLI wiring — quor gain --format/--by-tool/--by-file
# ---------------------------------------------------------------------------


class TestGainCliExport:
    def test_invalid_format_exits_with_error(self, tmp_path: Path) -> None:
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["gain", "--project", str(tmp_path), "--format", "xml"]
            )
        assert result.exit_code != 0
        assert "--format must be one of" in result.output

    def test_format_json_emits_valid_json_with_no_rich_markup(self, tmp_path: Path) -> None:
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="git status",
                project_path=tmp_path.as_posix(),
                original_tokens=100,
                final_tokens=20,
                filter_name="git-status",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["gain", "--project", str(tmp_path), "--format", "json"]
            )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["summary"]["tokens_saved"] == 80
        assert "[bold]" not in result.output

    def test_format_json_on_empty_project_is_clean_zeroed_payload(self, tmp_path: Path) -> None:
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["gain", "--project", str(tmp_path), "--format", "json"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["summary"]["total_operations"] == 0
        assert parsed["summary"]["tokens_saved"] == 0

    def test_format_csv_with_by_tool_and_by_file(self, tmp_path: Path) -> None:
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="Read: app.py",
                project_path=tmp_path.as_posix(),
                original_tokens=400,
                final_tokens=100,
                filter_name="cat-python",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app,
                [
                    "gain",
                    "--project",
                    str(tmp_path),
                    "--format",
                    "csv",
                    "--by-tool",
                    "--by-file",
                ],
            )

        assert result.exit_code == 0
        rows = list(csv.reader(io.StringIO(result.output)))
        assert ["cli", "1", "400", "100", "300", "75.0"] in rows
        assert ["app.py", "1", "400", "100", "300", "75.0"] in rows

    def test_table_format_by_tool_and_by_file_render(self, tmp_path: Path) -> None:
        from quor.tracking.db import InvocationRecord, TrackingDB

        db_path = tmp_path / "data" / "quor.db"
        db = TrackingDB(db_path=db_path)
        db.record(
            InvocationRecord(
                command="Read: app.py",
                project_path=tmp_path.as_posix(),
                original_tokens=400,
                final_tokens=100,
                filter_name="cat-python",
                was_passthrough=False,
                duration_ms=5.0,
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app,
                ["gain", "--project", str(tmp_path), "--by-tool", "--by-file"],
            )

        assert result.exit_code == 0
        assert "By tool" in result.output
        assert "Top files" in result.output
        assert "app.py" in result.output

    def test_by_tool_with_no_data_shows_clean_empty_note_not_traceback(
        self, tmp_path: Path
    ) -> None:
        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(
                app, ["gain", "--project", str(tmp_path), "--by-tool"]
            )
        assert result.exit_code == 0
        assert "Traceback" not in result.output
