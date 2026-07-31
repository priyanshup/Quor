"""Unit tests for quor/cli/commands/dashboard.py — `quor dashboard` (QB-083)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from quor.cli.commands.dashboard import (
    _estimated_cost_saved,
    _render,
    _trend_marker,
    _truncate,
)
from quor.cli.main import app
from quor.tracking.db import InvocationRecord, TrackingDB

runner = CliRunner()


def _seed(db_path: Path, project_path: Path, records: list[dict]) -> None:
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
                    r.get("project_path", project_path.as_posix()),
                    r.get("original_tokens", 100),
                    r.get("final_tokens", 20),
                    r.get("filter_name", "git"),
                    r.get("was_passthrough", 0),
                    r.get("duration_ms", 10.0),
                    r["recorded_at"],
                    1,
                ),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# _render() — direct unit tests, full control over `since`
# ---------------------------------------------------------------------------


class TestRender:
    def test_waiting_state_on_missing_db(self, tmp_path: Path) -> None:
        output = _render(tmp_path / "missing.db", tmp_path, datetime.now(UTC))
        text = _plain(output)
        assert "Waiting for activity" in text

    def test_populated_state_shows_headline_and_stats(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(
            db_path,
            tmp_path,
            [
                {
                    "command": "pytest tests/",
                    "filter_name": "pytest",
                    "original_tokens": 5000,
                    "final_tokens": 1200,
                    "recorded_at": since.isoformat(timespec="seconds"),
                }
            ],
        )
        text = _plain(_render(db_path, tmp_path, since))
        assert "SAVED SO FAR" in text
        assert "Commands processed" in text
        assert "1" in text
        assert "Top filters this session" in text
        assert "pytest" in text
        assert "Recent activity" in text
        assert "Estimated cost saved" in text
        assert "estimated via the char/4 approximation" in text
        assert "±20% versus a real tokenizer" in text

    def test_rows_before_since_are_excluded(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(
            db_path,
            tmp_path,
            [
                {
                    "command": "too old",
                    "recorded_at": (since - timedelta(seconds=1)).isoformat(timespec="seconds"),
                }
            ],
        )
        text = _plain(_render(db_path, tmp_path, since))
        assert "Waiting for activity" in text

    def test_passthrough_row_shown_in_stats(self, tmp_path: Path) -> None:
        """QB-091: `quor dashboard` previously omitted the Passthrough stat
        `quor gain` already showed — regression guard against that drift,
        now impossible since both share gain_presentation.build_stats_table()."""
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(
            db_path,
            tmp_path,
            [
                {
                    "filter_name": None,
                    "was_passthrough": 1,
                    "original_tokens": 30,
                    "final_tokens": 30,
                    "recorded_at": since.isoformat(timespec="seconds"),
                },
                {
                    "filter_name": "git",
                    "was_passthrough": 0,
                    "original_tokens": 100,
                    "final_tokens": 20,
                    "recorded_at": since.isoformat(timespec="seconds"),
                },
            ],
        )
        text = _plain(_render(db_path, tmp_path, since))
        assert "Passthrough" in text

    def test_eligible_line_shown_when_passthrough_present(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(
            db_path,
            tmp_path,
            [
                {
                    "filter_name": None,
                    "was_passthrough": 1,
                    "original_tokens": 30,
                    "final_tokens": 30,
                    "recorded_at": since.isoformat(timespec="seconds"),
                },
                {
                    "filter_name": "git",
                    "was_passthrough": 0,
                    "original_tokens": 100,
                    "final_tokens": 20,
                    "recorded_at": since.isoformat(timespec="seconds"),
                },
            ],
        )
        text = _plain(_render(db_path, tmp_path, since))
        assert "a filter could apply to" in text

    def test_low_sample_caveat_shown_for_small_session(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(db_path, tmp_path, [{"recorded_at": since.isoformat(timespec="seconds")}])
        text = _plain(_render(db_path, tmp_path, since))
        assert "early read" in text

    def test_no_low_sample_caveat_once_stable(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(
            db_path,
            tmp_path,
            [{"recorded_at": since.isoformat(timespec="seconds")} for _ in range(5)],
        )
        text = _plain(_render(db_path, tmp_path, since))
        assert "early read" not in text

    def test_trend_marker_appears_once_stable_with_previous_fraction(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(
            db_path,
            tmp_path,
            [{"recorded_at": since.isoformat(timespec="seconds")} for _ in range(5)],
        )
        # Default seeded rows save (100-20)/100 = 80% each; a previous
        # fraction well above that should render a down arrow.
        text = _plain(_render(db_path, tmp_path, since, previous_fraction=0.95))
        assert "▼" in text

    def test_no_trend_marker_on_first_render(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quor.db"
        since = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
        _seed(
            db_path,
            tmp_path,
            [{"recorded_at": since.isoformat(timespec="seconds")} for _ in range(5)],
        )
        text = _plain(_render(db_path, tmp_path, since))
        assert "▼" not in text
        assert "▲" not in text


# ---------------------------------------------------------------------------
# _trend_marker() — pure function
# ---------------------------------------------------------------------------


class TestTrendMarker:
    def test_no_previous_returns_empty(self) -> None:
        assert _trend_marker(0.5, None) == ""

    def test_increase_shows_up_arrow(self) -> None:
        assert "▲" in _trend_marker(0.5, 0.3)

    def test_decrease_shows_down_arrow(self) -> None:
        assert "▼" in _trend_marker(0.3, 0.5)

    def test_tiny_delta_shows_flat_dot_not_arrow(self) -> None:
        marker = _trend_marker(0.501, 0.500)
        assert "▲" not in marker
        assert "▼" not in marker
        assert "·" in marker


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


class TestEstimatedCostSaved:
    def test_positive_tokens(self) -> None:
        assert _estimated_cost_saved(1_000_000) == 3.00

    def test_negative_tokens_floor_to_zero(self) -> None:
        assert _estimated_cost_saved(-500) == 0.0


class TestTruncate:
    def test_short_text_unchanged(self) -> None:
        assert _truncate("short", 10) == "short"

    def test_long_text_truncated_with_ellipsis(self) -> None:
        result = _truncate("a" * 20, 10)
        assert len(result) == 10
        assert result.endswith("…")


# ---------------------------------------------------------------------------
# CLI wiring — `quor dashboard --once`
# ---------------------------------------------------------------------------


class TestDashboardCommand:
    def test_once_on_empty_db(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["dashboard", "--once", "--project", str(tmp_path)])
        assert result.exit_code == 0
        assert "Waiting for activity" in result.output

    def test_once_with_populated_db(self, tmp_path: Path) -> None:
        # CliRunner captures a non-tty stdout, so even without --once this
        # would take the same single-snapshot path — --once is used here to
        # assert the explicit, documented flag as well.
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
                # In the future relative to real wall-clock time, so it
                # lands after dashboard_command's own `session_start =
                # datetime.now()` captured when the CLI invocation runs.
                recorded_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(
                    timespec="seconds"
                ),
            )
        )
        db.flush()
        db.close()

        with patch("platformdirs.user_data_dir", return_value=str(tmp_path / "data")):
            result = runner.invoke(app, ["dashboard", "--once", "--project", str(tmp_path)])

        assert result.exit_code == 0
        assert "SAVED SO FAR" in result.output
        assert "estimated via the char/4 approximation" in result.output

    def test_non_terminal_fallback_without_once_flag(self, tmp_path: Path) -> None:
        """CliRunner's captured stdout isn't a tty — dashboard_command must
        take the single-snapshot path on its own, not hang in the Live loop,
        even when --once wasn't passed."""
        result = runner.invoke(app, ["dashboard", "--project", str(tmp_path)])
        assert result.exit_code == 0
        assert "Waiting for activity" in result.output


def _plain(renderable: object) -> str:
    """Render a Rich renderable to plain text for substring assertions."""
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    Console(file=buf, width=120, highlight=False).print(renderable)
    return buf.getvalue()
