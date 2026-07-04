"""Unit tests for quor/pipeline/tee.py — ADR-023 tee mechanism.

Isolated from real disk via the autouse `_isolate_platformdirs` fixture in
tests/conftest.py (patches platformdirs.user_data_dir to a per-test tmp dir).
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import platformdirs
import pytest

from quor.pipeline.tee import (
    MAX_CONSECUTIVE_TEE_FAILURES,
    cleanup_tee,
    content_hash,
    get_tee_status,
    record_tee_failure,
    record_tee_success,
    reset_tee_state,
    tee_dir,
    tee_path,
    write_tee,
)

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
# tee_path
# ---------------------------------------------------------------------------


class TestTeePath:
    def test_path_is_under_tee_dir(self) -> None:
        assert tee_path("some content").parent == tee_dir()

    def test_filename_is_hash_plus_txt(self) -> None:
        content = "some content"
        assert tee_path(content).name == f"{content_hash(content)}.txt"

    def test_same_content_same_path(self) -> None:
        assert tee_path("abc") == tee_path("abc")

    def test_different_content_different_path(self) -> None:
        assert tee_path("abc") != tee_path("xyz")

    def test_empty_content_boundary(self) -> None:
        """Empty string is a valid, hashable content value — no crash, and
        it resolves to a distinct, deterministic path like any other input."""
        path = tee_path("")
        assert path.parent == tee_dir()
        assert path.name == f"{content_hash('')}.txt"


class TestTeeDir:
    def test_tee_dir_is_under_user_data_dir(self) -> None:
        expected = Path(platformdirs.user_data_dir("quor")) / "tee"
        assert tee_dir() == expected

    def test_tee_dir_stable_across_calls(self) -> None:
        assert tee_dir() == tee_dir()


# ---------------------------------------------------------------------------
# write_tee
# ---------------------------------------------------------------------------


class TestWriteTee:
    def test_writes_file_with_content(self) -> None:
        path = write_tee("hello world")
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "hello world"

    def test_idempotent_no_duplicate_file(self) -> None:
        path1 = write_tee("identical content")
        path2 = write_tee("identical content")
        assert path1 == path2
        assert len(list(path1.parent.glob("*.txt"))) == 1

    def test_different_content_creates_separate_files(self) -> None:
        path1 = write_tee("content A")
        path2 = write_tee("content B")
        assert path1 != path2
        assert path1.exists()
        assert path2.exists()

    def test_empty_content_boundary(self) -> None:
        """Writing empty-string content must not crash — it's a legitimate,
        if unusual, value for the dispatcher to pass through."""
        path = write_tee("")
        assert path.exists()
        assert path.read_text(encoding="utf-8") == ""

    def test_large_content_boundary(self) -> None:
        """Multi-MB content (a large diff/log) must write correctly, not
        just small test-fixture-sized strings."""
        large = "line of realistic log output\n" * 100_000
        path = write_tee(large)
        assert path.exists()
        assert path.stat().st_size == len(large.encode("utf-8"))

    def test_no_newline_translation_regression(self) -> None:
        """Regression guard: os.open() must be opened in binary mode. On
        Windows, os.open() defaults to text mode and silently rewrites every
        "\\n" to "\\r\\n" on write, which both violates ADR-023's "no
        modification" guarantee and makes the on-disk bytes no longer match
        the SHA256 used to name the file. Found during the QB test-hardening
        pass; fixed by OR-ing in os.O_BINARY (a no-op on POSIX)."""
        content = "line one\nline two\nline three\n"
        path = write_tee(content)
        raw_bytes = path.read_bytes()
        assert raw_bytes == content.encode("utf-8")
        assert b"\r\n" not in raw_bytes

    def test_cache_hit_refreshes_mtime(self) -> None:
        path = write_tee("content to refresh")
        old_time = time.time() - 1000
        os.utime(path, (old_time, old_time))
        assert path.stat().st_mtime == pytest.approx(old_time, abs=1)

        write_tee("content to refresh")  # cache hit — same content again

        assert path.stat().st_mtime > old_time

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits not enforced on Windows")
    def test_posix_permissions_are_owner_only(self) -> None:
        path = write_tee("owner-only content")
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# cleanup_tee
# ---------------------------------------------------------------------------


class TestCleanupTee:
    def test_deletes_files_older_than_max_age(self) -> None:
        path = write_tee("stale content")
        stale_time = time.time() - (8 * 86400)  # 8 days old
        os.utime(path, (stale_time, stale_time))

        cleanup_tee(max_age_days=7, throttle_hours=0)

        assert not path.exists()

    def test_keeps_files_within_max_age(self) -> None:
        path = write_tee("fresh content")

        cleanup_tee(max_age_days=7, throttle_hours=0)

        assert path.exists()

    def test_missing_tee_dir_does_not_raise(self) -> None:
        # No tee file has ever been written in this test — tee_dir() doesn't exist.
        cleanup_tee(max_age_days=7, throttle_hours=0)

    def test_throttle_skips_repeated_cleanup(self) -> None:
        # First call: no prior state recorded, so it always runs regardless of throttle.
        path1 = write_tee("stale content 1")
        stale_time = time.time() - (8 * 86400)
        os.utime(path1, (stale_time, stale_time))
        cleanup_tee(max_age_days=7, throttle_hours=24)
        assert not path1.exists()

        # Second call, immediately after: throttle window (24h) has not elapsed,
        # so this run must be a no-op even though path2 is stale.
        path2 = write_tee("stale content 2")
        os.utime(path2, (stale_time, stale_time))
        cleanup_tee(max_age_days=7, throttle_hours=24)
        assert path2.exists()

    def test_throttle_expired_runs_again(self) -> None:
        path1 = write_tee("stale content 3")
        stale_time = time.time() - (8 * 86400)
        os.utime(path1, (stale_time, stale_time))
        # throttle_hours=0 means "never throttled" for this call.
        cleanup_tee(max_age_days=7, throttle_hours=0)
        assert not path1.exists()

        path2 = write_tee("stale content 4")
        os.utime(path2, (stale_time, stale_time))
        cleanup_tee(max_age_days=7, throttle_hours=0)
        assert not path2.exists()

    def test_state_db_uses_wal_mode(self) -> None:
        """Regression guard: concurrent first-opens of tee_state.db must not
        hit the same "PRAGMA journal_mode=WAL requires exclusive lock" bug
        TrackingDB hit in Phase 7 — the connection must actually be in WAL
        mode after cleanup_tee() runs."""
        cleanup_tee(max_age_days=7, throttle_hours=0)

        state_path = Path(platformdirs.user_data_dir("quor")) / "tee_state.db"
        assert state_path.exists()
        conn = sqlite3.connect(str(state_path))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        assert mode.lower() == "wal"


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
