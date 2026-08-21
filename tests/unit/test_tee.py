"""Unit tests for quor/pipeline/tee.py — ADR-023 tee mechanism.

QB-123: content lives in a single bounded `tee_logs` SQLite table (inside
`tee_state.db`) instead of individual files — these tests assert against
that table directly (via read_tee()/a raw connection) rather than the
filesystem.

Isolated from real disk via the autouse `_isolate_platformdirs` fixture in
tests/conftest.py (patches platformdirs.user_data_dir to a per-test tmp dir).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import platformdirs
import pytest

from quor.pipeline.tee import (
    MAX_CONSECUTIVE_TEE_FAILURES,
    cleanup_tee,
    content_hash,
    current_tee_size_bytes,
    get_tee_status,
    read_tee,
    record_tee_failure,
    record_tee_success,
    reset_tee_state,
    write_tee,
)


def _state_db_path() -> Path:
    return Path(platformdirs.user_data_dir("quor")) / "tee_state.db"


def _row_count() -> int:
    path = _state_db_path()
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tee_logs "
            "(content_hash TEXT PRIMARY KEY, content BLOB NOT NULL, "
            "size_bytes INTEGER NOT NULL, last_used_at REAL NOT NULL)"
        )
        return int(conn.execute("SELECT COUNT(*) FROM tee_logs").fetchone()[0])
    finally:
        conn.close()


def _set_last_used_at(digest: str, when: float) -> None:
    conn = sqlite3.connect(str(_state_db_path()))
    try:
        conn.execute("UPDATE tee_logs SET last_used_at = ? WHERE content_hash = ?", (when, digest))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_deterministic(self) -> None:
        assert content_hash("hello") == content_hash("hello")

    def test_different_content_different_hash(self) -> None:
        assert content_hash("hello") != content_hash("world")

    def test_is_sha256_hex_digest(self) -> None:
        digest = content_hash("hello")
        assert len(digest) == 64
        int(digest, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# write_tee / read_tee — no filesystem involved (QB-123)
# ---------------------------------------------------------------------------


class TestWriteTee:
    def test_returns_content_hash(self) -> None:
        digest = write_tee("hello world")
        assert digest == content_hash("hello world")

    def test_no_new_files_created_on_disk(self) -> None:
        """The whole point of QB-123: writing a tee entry must not create
        any individual file — only rows inside the single tee_state.db."""
        data_dir = Path(platformdirs.user_data_dir("quor"))
        before = set(data_dir.rglob("*")) if data_dir.exists() else set()

        write_tee("hello world")

        after = set(data_dir.rglob("*"))
        new_paths = after - before
        # The only new filesystem entries permitted are the state db itself
        # (and its WAL/SHM sidecar files) and the freshly created data dir.
        allowed_names = {"tee_state.db", "tee_state.db-wal", "tee_state.db-shm", "tee_state.db-journal"}
        unexpected = [p for p in new_paths if p.is_file() and p.name not in allowed_names]
        assert unexpected == []

    def test_content_readable_back(self) -> None:
        digest = write_tee("hello world")
        assert read_tee(digest) == "hello world"

    def test_idempotent_no_duplicate_row(self) -> None:
        digest1 = write_tee("identical content")
        digest2 = write_tee("identical content")
        assert digest1 == digest2
        assert _row_count() == 1

    def test_different_content_creates_separate_rows(self) -> None:
        digest1 = write_tee("content A")
        digest2 = write_tee("content B")
        assert digest1 != digest2
        assert read_tee(digest1) == "content A"
        assert read_tee(digest2) == "content B"

    def test_empty_content_boundary(self) -> None:
        """Writing empty-string content must not crash — it's a legitimate,
        if unusual, value for the dispatcher to pass through."""
        digest = write_tee("")
        assert read_tee(digest) == ""

    def test_large_content_boundary(self) -> None:
        """Multi-MB content (a large diff/log) must write correctly, not
        just small test-fixture-sized strings."""
        large = "line of realistic log output\n" * 100_000
        digest = write_tee(large)
        assert read_tee(digest) == large

    def test_no_newline_translation(self) -> None:
        """SQLite BLOB storage has no OS text-mode translation to guard
        against — regression guard that content round-trips byte-exact,
        including embedded \\n, replacing the old file-based _O_BINARY test."""
        content = "line one\nline two\nline three\n"
        digest = write_tee(content)
        assert read_tee(digest) == content
        assert "\r\n" not in (read_tee(digest) or "")

    def test_cache_hit_refreshes_last_used_at(self) -> None:
        digest = write_tee("content to refresh")
        old_time = time.time() - 1000
        _set_last_used_at(digest, old_time)

        write_tee("content to refresh")  # cache hit — same content again

        conn = sqlite3.connect(str(_state_db_path()))
        try:
            row = conn.execute(
                "SELECT last_used_at FROM tee_logs WHERE content_hash = ?", (digest,)
            ).fetchone()
        finally:
            conn.close()
        assert row[0] > old_time


class TestReadTee:
    def test_unknown_hash_returns_none(self) -> None:
        assert read_tee("0" * 64) is None

    def test_no_state_db_returns_none(self) -> None:
        # Nothing has ever been teed in this test — tee_state.db doesn't exist.
        assert read_tee(content_hash("never written")) is None


# ---------------------------------------------------------------------------
# cleanup_tee
# ---------------------------------------------------------------------------


class TestCleanupTee:
    def test_deletes_rows_older_than_max_age(self) -> None:
        digest = write_tee("stale content")
        stale_time = time.time() - (8 * 86400)  # 8 days old
        _set_last_used_at(digest, stale_time)

        cleanup_tee(max_age_days=7, throttle_hours=0)

        assert read_tee(digest) is None

    def test_keeps_rows_within_max_age(self) -> None:
        digest = write_tee("fresh content")

        cleanup_tee(max_age_days=7, throttle_hours=0)

        assert read_tee(digest) == "fresh content"

    def test_missing_state_db_does_not_raise(self) -> None:
        # No tee entry has ever been written in this test — tee_state.db
        # doesn't exist yet.
        cleanup_tee(max_age_days=7, throttle_hours=0)

    def test_throttle_skips_repeated_cleanup(self) -> None:
        # First call: no prior state recorded, so it always runs regardless of throttle.
        digest1 = write_tee("stale content 1")
        stale_time = time.time() - (8 * 86400)
        _set_last_used_at(digest1, stale_time)
        cleanup_tee(max_age_days=7, throttle_hours=24)
        assert read_tee(digest1) is None

        # Second call, immediately after: throttle window (24h) has not elapsed,
        # so this run must be a no-op even though digest2 is stale.
        digest2 = write_tee("stale content 2")
        _set_last_used_at(digest2, stale_time)
        cleanup_tee(max_age_days=7, throttle_hours=24)
        assert read_tee(digest2) == "stale content 2"

    def test_throttle_expired_runs_again(self) -> None:
        digest1 = write_tee("stale content 3")
        stale_time = time.time() - (8 * 86400)
        _set_last_used_at(digest1, stale_time)
        # throttle_hours=0 means "never throttled" for this call.
        cleanup_tee(max_age_days=7, throttle_hours=0)
        assert read_tee(digest1) is None

        digest2 = write_tee("stale content 4")
        _set_last_used_at(digest2, stale_time)
        cleanup_tee(max_age_days=7, throttle_hours=0)
        assert read_tee(digest2) is None

    def test_state_db_uses_wal_mode(self) -> None:
        """Regression guard: concurrent first-opens of tee_state.db must not
        hit the same "PRAGMA journal_mode=WAL requires exclusive lock" bug
        TrackingDB hit in Phase 7 — the connection must actually be in WAL
        mode after cleanup_tee() runs."""
        cleanup_tee(max_age_days=7, throttle_hours=0)

        state_path = _state_db_path()
        assert state_path.exists()
        conn = sqlite3.connect(str(state_path))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        assert mode.lower() == "wal"


# ---------------------------------------------------------------------------
# cleanup_tee — size ceiling (QB-103)
# ---------------------------------------------------------------------------


class TestCleanupTeeSizeLimit:
    def test_under_limit_nothing_deleted(self) -> None:
        digest = write_tee("a" * 100)
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=1000)
        assert read_tee(digest) is not None

    def test_exactly_at_limit_nothing_deleted(self) -> None:
        content = "a" * 100
        digest = write_tee(content)
        size = len(content.encode("utf-8"))
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=size)
        assert read_tee(digest) is not None

    def test_over_limit_deletes_oldest_first(self) -> None:
        old_digest = write_tee("a" * 100)
        old_time = time.time() - 100
        _set_last_used_at(old_digest, old_time)

        new_digest = write_tee("b" * 100)

        # Total is 200 bytes; budget only fits one entry.
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=100)

        assert read_tee(old_digest) is None
        assert read_tee(new_digest) is not None

    def test_eviction_stops_once_back_within_budget(self) -> None:
        digests = []
        for i in range(5):
            d = write_tee(f"content-{i}" * 10)
            t = time.time() - (100 - i)  # ascending times: 0 oldest ... 4 newest
            _set_last_used_at(d, t)
            digests.append(d)

        per_entry_size = len((f"content-{0}" * 10).encode("utf-8"))
        budget = per_entry_size * 3  # room for exactly the 3 newest entries

        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=budget)

        assert read_tee(digests[0]) is None
        assert read_tee(digests[1]) is None
        assert read_tee(digests[2]) is not None
        assert read_tee(digests[3]) is not None
        assert read_tee(digests[4]) is not None

    def test_newer_rows_survive_when_older_ones_satisfy_budget(self) -> None:
        old_digest = write_tee("old" * 50)
        old_time = time.time() - 500
        _set_last_used_at(old_digest, old_time)

        new_digest = write_tee("new" * 50)

        total = len(("old" * 50).encode("utf-8")) + len(("new" * 50).encode("utf-8"))
        # Budget just under the total: exactly one entry (the older one)
        # needs to go to satisfy it.
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=total - 1)

        assert read_tee(old_digest) is None
        assert read_tee(new_digest) is not None

    def test_single_row_larger_than_budget_is_evicted_deterministically(self) -> None:
        """No special-casing for a lone oversized row: the ceiling is a
        hard bound, so a single survivor over budget is evicted like any
        other entry — deterministic, no crash, no infinite loop."""
        big_digest = write_tee("x" * 10_000)

        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=100)

        assert read_tee(big_digest) is None

    def test_age_and_size_eviction_together(self) -> None:
        # Stale — removed by age eviction regardless of the size budget.
        stale_digest = write_tee("stale" * 20)
        stale_time = time.time() - (8 * 86400)
        _set_last_used_at(stale_digest, stale_time)

        # Fresh, but old enough among survivors to be size-evicted.
        older_fresh_digest = write_tee("older-fresh" * 20)
        older_fresh_time = time.time() - 500
        _set_last_used_at(older_fresh_digest, older_fresh_time)

        # Fresh and newest — must survive both passes.
        newest_digest = write_tee("newest" * 20)

        newest_size = len(("newest" * 20).encode("utf-8"))
        # Budget only fits the single newest survivor once age eviction has
        # already removed the stale entry.
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=newest_size)

        assert read_tee(stale_digest) is None
        assert read_tee(older_fresh_digest) is None
        assert read_tee(newest_digest) is not None

    def test_default_max_bytes_used_when_not_specified(self) -> None:
        """A small entry, well under the 500MB default, must survive a
        default-argument cleanup_tee() call — regression guard that adding
        max_bytes didn't change default behavior for ordinary use."""
        digest = write_tee("small content")
        cleanup_tee(max_age_days=7, throttle_hours=0)
        assert read_tee(digest) is not None

    def test_size_eviction_respects_throttle(self) -> None:
        """Size-triggered eviction must only happen when cleanup actually
        runs — a throttled (no-op) call must not evict anything, even when
        the cache is far over budget."""
        old_digest = write_tee("a" * 100)
        old_time = time.time() - 100
        _set_last_used_at(old_digest, old_time)
        b_digest = write_tee("b" * 100)

        # First call: over budget (200 > 100), and no prior throttle state
        # recorded yet, so it always runs — evicts the older of the two.
        cleanup_tee(max_age_days=7, throttle_hours=24, max_bytes=100)
        assert read_tee(old_digest) is None
        assert read_tee(b_digest) is not None

        c_digest = write_tee("c" * 100)
        # Over budget again (b + c = 200 > 100), but the throttle window
        # (24h) has not elapsed since the first call — must be a no-op.
        cleanup_tee(max_age_days=7, throttle_hours=24, max_bytes=100)
        assert read_tee(b_digest) is not None
        assert read_tee(c_digest) is not None

    def test_size_eviction_smoke_with_many_rows(self) -> None:
        """Non-timing-asserting smoke test: a few thousand distinct rows
        must not trigger pathological behavior, and the eviction result must
        still be correct at this scale. No timing threshold is asserted
        (would be flaky in CI) — only correctness."""
        n = 3000
        digests = []
        for i in range(n):
            d = write_tee(f"{i:06d}-" + ("x" * 50))
            t = time.time() - (n - i)  # strictly ascending times
            _set_last_used_at(d, t)
            digests.append(d)

        keep_count = 100
        per_entry_size = len((f"{0:06d}-" + ("x" * 50)).encode("utf-8"))
        budget = per_entry_size * keep_count

        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=budget)

        assert current_tee_size_bytes() <= budget
        assert read_tee(digests[-1]) is not None  # newest survives
        assert read_tee(digests[0]) is None  # clearly-oldest is gone


# ---------------------------------------------------------------------------
# current_tee_size_bytes (QB-103)
# ---------------------------------------------------------------------------


class TestCurrentTeeSizeBytes:
    def test_empty_returns_zero(self) -> None:
        assert current_tee_size_bytes() == 0

    def test_sums_all_row_sizes(self) -> None:
        write_tee("a" * 100)
        write_tee("b" * 250)

        assert current_tee_size_bytes() == 350

    def test_does_not_delete_anything(self) -> None:
        digest = write_tee("content")
        current_tee_size_bytes()
        assert read_tee(digest) is not None


# ---------------------------------------------------------------------------
# quor doctor — _check_tee_size (QB-103)
# ---------------------------------------------------------------------------


class TestCheckTeeSize:
    def test_reports_size_and_limit_within_budget(self) -> None:
        from quor.cli.commands.doctor import Status, _check_tee_size
        from quor.config.model import QuorUserConfig

        write_tee("a" * 1000)
        user_config = QuorUserConfig(tee_max_bytes=1_000_000)

        name, status, detail = _check_tee_size(user_config)

        expected_size_mb = 1000 / (1024 * 1024)
        expected_limit_mb = 1_000_000 / (1024 * 1024)
        assert name == "Tee cache size"
        assert status is Status.PASS
        assert detail == f"{expected_size_mb:.1f} MB used of {expected_limit_mb:.0f} MB limit"

    def test_reports_over_limit(self) -> None:
        from quor.cli.commands.doctor import Status, _check_tee_size
        from quor.config.model import QuorUserConfig

        write_tee("a" * 10_000)
        user_config = QuorUserConfig(tee_max_bytes=100)

        name, status, detail = _check_tee_size(user_config)

        expected_size_mb = 10_000 / (1024 * 1024)
        expected_limit_mb = 100 / (1024 * 1024)
        assert name == "Tee cache size"
        assert status is Status.WARN  # advisory only — must never fail doctor
        assert detail == (
            f"{expected_size_mb:.1f} MB used, over the {expected_limit_mb:.0f} MB limit — "
            "will be trimmed on the next scheduled cleanup"
        )


# ---------------------------------------------------------------------------
# quor doctor --show-tee (QB-123)
# ---------------------------------------------------------------------------


class TestShowTee:
    def test_show_tee_prints_content(self, capsys: pytest.CaptureFixture[str]) -> None:
        from quor.cli.commands.doctor import _show_tee

        digest = write_tee("the recovered content")

        _show_tee(digest)

        assert capsys.readouterr().out.strip() == "the recovered content"

    def test_show_tee_unknown_hash_exits_nonzero(self) -> None:
        import typer

        from quor.cli.commands.doctor import _show_tee

        with pytest.raises(typer.Exit):
            _show_tee("0" * 64)


# ---------------------------------------------------------------------------
# Adaptive fallback: get_tee_status / record_tee_failure / record_tee_success
# / reset_tee_state
# ---------------------------------------------------------------------------


class TestGetTeeStatus:
    def test_default_status_is_enabled_with_no_failures(self) -> None:
        """Nothing has ever failed (no state file exists yet) — must default
        to enabled, not disabled."""
        status = get_tee_status()
        assert status.disabled is False
        assert status.consecutive_failures == 0
        assert status.disabled_reason is None


class TestRecordTeeFailure:
    def test_one_failure_short_of_threshold_leaves_tee_enabled(self) -> None:
        """Anything short of MAX_CONSECUTIVE_TEE_FAILURES must not disable
        tee — only reaching the threshold itself does."""
        for _ in range(MAX_CONSECUTIVE_TEE_FAILURES - 1):
            record_tee_failure("PermissionError: Access is denied")
        status = get_tee_status()
        assert status.disabled is False
        assert status.consecutive_failures == MAX_CONSECUTIVE_TEE_FAILURES - 1

    def test_reaching_threshold_disables_tee(self) -> None:
        for _ in range(MAX_CONSECUTIVE_TEE_FAILURES):
            record_tee_failure("PermissionError: Access is denied")
        status = get_tee_status()
        assert status.disabled is True
        assert status.consecutive_failures == MAX_CONSECUTIVE_TEE_FAILURES
        assert status.disabled_reason == "PermissionError: Access is denied"

    def test_disabled_reason_reflects_the_triggering_failure(self) -> None:
        for _ in range(MAX_CONSECUTIVE_TEE_FAILURES - 1):
            record_tee_failure("an earlier error")
        record_tee_failure("the triggering error")
        assert get_tee_status().disabled_reason == "the triggering error"

    def test_disabled_state_survives_a_fresh_read(self) -> None:
        """get_tee_status() has no in-memory cache — every call is a fresh
        read from disk. Calling it again (as a new `quor` process would
        after a restart) must still see the persisted disabled state."""
        for _ in range(MAX_CONSECUTIVE_TEE_FAILURES):
            record_tee_failure("disk full")
        assert get_tee_status().disabled is True
        assert get_tee_status().disabled is True  # second, independent read


class TestRecordTeeSuccess:
    def test_success_resets_counter_after_one_failure(self) -> None:
        record_tee_failure("transient error")
        assert get_tee_status().consecutive_failures == 1

        record_tee_success()

        status = get_tee_status()
        assert status.consecutive_failures == 0
        assert status.disabled is False

    def test_success_with_no_prior_failures_is_a_noop(self) -> None:
        record_tee_success()
        status = get_tee_status()
        assert status.consecutive_failures == 0
        assert status.disabled is False


class TestResetTeeState:
    def test_reset_clears_disabled_state_and_counter(self) -> None:
        for _ in range(MAX_CONSECUTIVE_TEE_FAILURES):
            record_tee_failure("x")
        assert get_tee_status().disabled is True

        reset_tee_state()

        status = get_tee_status()
        assert status.disabled is False
        assert status.consecutive_failures == 0
        assert status.disabled_reason is None

    def test_reset_with_no_prior_state_is_a_noop(self) -> None:
        reset_tee_state()
        status = get_tee_status()
        assert status.disabled is False
        assert status.consecutive_failures == 0
