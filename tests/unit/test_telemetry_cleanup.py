"""Unit tests for quor/tracking/db.py's QB-128 retention sweep:
prune_stale_invocations() / prune_stale_invocations_safe() / count_invocations().

Isolated from real disk via the autouse `_isolate_platformdirs` fixture in
tests/conftest.py (patches platformdirs.user_data_dir to a per-test tmp
dir), same convention as tests/unit/test_tee.py /
tests/unit/test_repo_intel_cleanup.py.
"""

from __future__ import annotations

import sqlite3
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import platformdirs
import pytest

from quor.tracking.db import (
    count_invocations,
    prune_stale_invocations,
    prune_stale_invocations_safe,
)

_SCHEMA_SQL = (
    Path(__file__).parent.parent.parent / "quor" / "tracking" / "schema.sql"
).read_text(encoding="utf-8")


def _seed_invocations(db_path: Path, records: list[dict]) -> None:
    """Write rows directly against schema.sql, bypassing TrackingDB's
    background thread — same approach tests/unit/test_tracking.py's own
    `_seed_invocations()` uses, kept standalone here for the same reason
    that file keeps its own copy rather than a shared import."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_SCHEMA_SQL)
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


def _db_path() -> Path:
    return Path(platformdirs.user_data_dir("quor")) / "quor.db"


def _row_count(db_path: Path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM invocations").fetchone()[0])


def _iso_days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# count_invocations
# ---------------------------------------------------------------------------


class TestCountInvocations:
    def test_zero_when_db_does_not_exist(self) -> None:
        assert count_invocations(_db_path()) == 0

    def test_counts_all_rows_regardless_of_age(self) -> None:
        db_path = _db_path()
        _seed_invocations(
            db_path,
            [
                {"recorded_at": _iso_days_ago(1)},
                {"recorded_at": _iso_days_ago(200)},
            ],
        )
        assert count_invocations(db_path) == 2


# ---------------------------------------------------------------------------
# prune_stale_invocations — age eviction
# ---------------------------------------------------------------------------


class TestPruneStaleInvocationsAgeEviction:
    def test_records_past_cutoff_are_evicted(self) -> None:
        db_path = _db_path()
        _seed_invocations(
            db_path,
            [
                {"command": "stale-1", "recorded_at": _iso_days_ago(100)},
                {"command": "stale-2", "recorded_at": _iso_days_ago(91)},
            ],
        )

        prune_stale_invocations(db_path, max_age_days=90)

        assert _row_count(db_path) == 0

    def test_records_within_window_survive(self) -> None:
        db_path = _db_path()
        _seed_invocations(
            db_path,
            [
                {"command": "fresh-1", "recorded_at": _iso_days_ago(1)},
                {"command": "fresh-2", "recorded_at": _iso_days_ago(89)},
            ],
        )

        prune_stale_invocations(db_path, max_age_days=90)

        assert _row_count(db_path) == 2

    def test_mixed_ages_only_evicts_stale_rows(self) -> None:
        db_path = _db_path()
        _seed_invocations(
            db_path,
            [
                {"command": "keep", "recorded_at": _iso_days_ago(10)},
                {"command": "evict", "recorded_at": _iso_days_ago(500)},
            ],
        )

        prune_stale_invocations(db_path, max_age_days=90)

        with sqlite3.connect(str(db_path)) as conn:
            remaining = [row[0] for row in conn.execute("SELECT command FROM invocations")]
        assert remaining == ["keep"]

    def test_no_op_when_db_does_not_exist(self) -> None:
        # Must not raise or create the file just by being called.
        prune_stale_invocations(_db_path(), max_age_days=90)
        assert not _db_path().exists()

    def test_configurable_max_age_days_is_honored(self) -> None:
        """QB-128: the retention window is no longer a hardcoded 90 —
        confirms a caller-supplied value (as QuorUserConfig.telemetry_max_age_days
        would be) actually changes what gets evicted."""
        db_path = _db_path()
        _seed_invocations(db_path, [{"command": "medium-age", "recorded_at": _iso_days_ago(10)}])

        prune_stale_invocations(db_path, max_age_days=5)

        assert _row_count(db_path) == 0


# ---------------------------------------------------------------------------
# prune_stale_invocations — 24h throttle
# ---------------------------------------------------------------------------


class TestPruneStaleInvocationsThrottle:
    def test_second_call_within_throttle_window_is_a_no_op(self) -> None:
        db_path = _db_path()
        _seed_invocations(db_path, [{"command": "first-stale", "recorded_at": _iso_days_ago(100)}])
        prune_stale_invocations(db_path, max_age_days=90)
        assert _row_count(db_path) == 0

        # A fresh stale row inserted right after the first (throttled) sweep
        # must survive the second call — the throttle should short-circuit
        # before the DELETE runs at all.
        _seed_invocations(db_path, [{"command": "second-stale", "recorded_at": _iso_days_ago(100)}])
        prune_stale_invocations(db_path, max_age_days=90)

        assert _row_count(db_path) == 1

    def test_call_after_throttle_window_elapses_runs_again(self) -> None:
        db_path = _db_path()
        _seed_invocations(db_path, [{"command": "first-stale", "recorded_at": _iso_days_ago(100)}])
        prune_stale_invocations(db_path, max_age_days=90)
        assert _row_count(db_path) == 0

        # Backdate the throttle's own last-run timestamp in the shared state
        # db so the next call sees the window as elapsed, mirroring
        # test_tee.py's own approach of manipulating throttle state directly
        # rather than sleeping in a test.
        state_path = Path(platformdirs.user_data_dir("quor")) / "tee_state.db"
        with sqlite3.connect(str(state_path)) as conn:
            conn.execute(
                "UPDATE telemetry_cleanup SET last_telemetry_cleanup_timestamp = ?",
                ((datetime.now(UTC) - timedelta(hours=25)).isoformat(),),
            )
            conn.commit()

        _seed_invocations(db_path, [{"command": "second-stale", "recorded_at": _iso_days_ago(100)}])
        prune_stale_invocations(db_path, max_age_days=90)

        assert _row_count(db_path) == 0

    def test_custom_throttle_hours_is_honored(self) -> None:
        db_path = _db_path()
        _seed_invocations(db_path, [{"command": "stale", "recorded_at": _iso_days_ago(100)}])

        prune_stale_invocations(db_path, max_age_days=90, throttle_hours=1)
        assert _row_count(db_path) == 0

        state_path = Path(platformdirs.user_data_dir("quor")) / "tee_state.db"
        with sqlite3.connect(str(state_path)) as conn:
            conn.execute(
                "UPDATE telemetry_cleanup SET last_telemetry_cleanup_timestamp = ?",
                ((datetime.now(UTC) - timedelta(hours=2)).isoformat(),),
            )
            conn.commit()

        _seed_invocations(db_path, [{"command": "stale-again", "recorded_at": _iso_days_ago(100)}])
        prune_stale_invocations(db_path, max_age_days=90, throttle_hours=1)

        assert _row_count(db_path) == 0


# ---------------------------------------------------------------------------
# prune_stale_invocations_safe — fail-open resilience
# ---------------------------------------------------------------------------


class TestPruneStaleInvocationsSafeFailOpen:
    def test_does_not_raise_when_database_is_locked(self) -> None:
        """Simulates the exact scenario QB-128 asked for: a lock/contention
        error during the sweep must never propagate to the caller (dispatch
        or an MCP tool call), matching tests/unit/test_fail_open.py's own
        `patch("sqlite3.connect", side_effect=...)` convention."""
        db_path = _db_path()
        _seed_invocations(db_path, [{"recorded_at": _iso_days_ago(100)}])

        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")):
            prune_stale_invocations_safe()  # must not raise

    def test_warns_on_failure(self) -> None:
        db_path = _db_path()
        _seed_invocations(db_path, [{"recorded_at": _iso_days_ago(100)}])

        with (
            patch("sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            prune_stale_invocations_safe()

        assert any("telemetry cleanup error" in str(w.message) for w in caught)

    def test_does_not_raise_on_permission_error(self) -> None:
        db_path = _db_path()
        _seed_invocations(db_path, [{"recorded_at": _iso_days_ago(100)}])

        with patch("sqlite3.connect", side_effect=PermissionError("no db access")):
            prune_stale_invocations_safe()  # must not raise

    def test_succeeds_and_evicts_under_normal_conditions(self) -> None:
        """The non-chaos path: prune_stale_invocations_safe() resolves
        quor.db's real path and the configured (default 90-day) retention
        window on its own, with no arguments."""
        db_path = _db_path()
        _seed_invocations(db_path, [{"command": "stale", "recorded_at": _iso_days_ago(100)}])

        prune_stale_invocations_safe()

        assert _row_count(db_path) == 0

    def test_honors_configured_telemetry_max_age_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from quor.config.model import QuorUserConfig

        monkeypatch.setattr(
            "quor.config.loader.load_user_config",
            lambda: QuorUserConfig(telemetry_max_age_days=5),
        )

        db_path = _db_path()
        _seed_invocations(db_path, [{"command": "medium-age", "recorded_at": _iso_days_ago(10)}])

        prune_stale_invocations_safe()

        assert _row_count(db_path) == 0
