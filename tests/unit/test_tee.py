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
# cleanup_tee — size ceiling (QB-103)
# ---------------------------------------------------------------------------


class TestCleanupTeeSizeLimit:
    def test_under_limit_nothing_deleted(self) -> None:
        path = write_tee("a" * 100)
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=1000)
        assert path.exists()

    def test_exactly_at_limit_nothing_deleted(self) -> None:
        content = "a" * 100
        path = write_tee(content)
        size = len(content.encode("utf-8"))
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=size)
        assert path.exists()

    def test_over_limit_deletes_oldest_first(self) -> None:
        old_path = write_tee("a" * 100)
        old_time = time.time() - 100
        os.utime(old_path, (old_time, old_time))

        new_path = write_tee("b" * 100)

        # Total is 200 bytes; budget only fits one file.
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=100)

        assert not old_path.exists()
        assert new_path.exists()

    def test_eviction_stops_once_back_within_budget(self) -> None:
        paths = []
        for i in range(5):
            p = write_tee(f"content-{i}" * 10)
            t = time.time() - (100 - i)  # ascending mtimes: 0 oldest ... 4 newest
            os.utime(p, (t, t))
            paths.append(p)

        per_file_size = paths[0].stat().st_size
        budget = per_file_size * 3  # room for exactly the 3 newest files

        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=budget)

        assert not paths[0].exists()
        assert not paths[1].exists()
        assert paths[2].exists()
        assert paths[3].exists()
        assert paths[4].exists()

    def test_newer_files_survive_when_older_ones_satisfy_budget(self) -> None:
        old_path = write_tee("old" * 50)
        old_time = time.time() - 500
        os.utime(old_path, (old_time, old_time))

        new_path = write_tee("new" * 50)

        total = old_path.stat().st_size + new_path.stat().st_size
        # Budget just under the total: exactly one file (the older one)
        # needs to go to satisfy it.
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=total - 1)

        assert not old_path.exists()
        assert new_path.exists()

    def test_single_file_larger_than_budget_is_evicted_deterministically(self) -> None:
        """No special-casing for a lone oversized file: the ceiling is a
        hard bound, so a single survivor over budget is evicted like any
        other entry — deterministic, no crash, no infinite loop."""
        big_path = write_tee("x" * 10_000)

        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=100)

        assert not big_path.exists()

    def test_age_and_size_eviction_together(self) -> None:
        # Stale — removed by age eviction regardless of the size budget.
        stale_path = write_tee("stale" * 20)
        stale_time = time.time() - (8 * 86400)
        os.utime(stale_path, (stale_time, stale_time))

        # Fresh, but old enough among survivors to be size-evicted.
        older_fresh_path = write_tee("older-fresh" * 20)
        older_fresh_time = time.time() - 500
        os.utime(older_fresh_path, (older_fresh_time, older_fresh_time))

        # Fresh and newest — must survive both passes.
        newest_path = write_tee("newest" * 20)

        newest_size = newest_path.stat().st_size
        # Budget only fits the single newest survivor once age eviction has
        # already removed the stale file.
        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=newest_size)

        assert not stale_path.exists()
        assert not older_fresh_path.exists()
        assert newest_path.exists()

    def test_default_max_bytes_used_when_not_specified(self) -> None:
        """A small file, well under the 500MB default, must survive a
        default-argument cleanup_tee() call — regression guard that adding
        max_bytes didn't change default behavior for ordinary use."""
        path = write_tee("small content")
        cleanup_tee(max_age_days=7, throttle_hours=0)
        assert path.exists()

    def test_size_eviction_respects_throttle(self) -> None:
        """Size-triggered eviction must only happen when cleanup actually
        runs — a throttled (no-op) call must not evict anything, even when
        the cache is far over budget."""
        old_path = write_tee("a" * 100)
        old_time = time.time() - 100
        os.utime(old_path, (old_time, old_time))
        b_path = write_tee("b" * 100)

        # First call: over budget (200 > 100), and no prior throttle state
        # recorded yet, so it always runs — evicts the older of the two.
        cleanup_tee(max_age_days=7, throttle_hours=24, max_bytes=100)
        assert not old_path.exists()
        assert b_path.exists()

        c_path = write_tee("c" * 100)
        # Over budget again (b + c = 200 > 100), but the throttle window
        # (24h) has not elapsed since the first call — must be a no-op.
        cleanup_tee(max_age_days=7, throttle_hours=24, max_bytes=100)
        assert b_path.exists()
        assert c_path.exists()

    def test_size_eviction_smoke_with_many_files(self) -> None:
        """Non-timing-asserting smoke test: a few thousand distinct files
        must not trigger pathological behavior, and the eviction result must
        still be correct at this scale. No timing threshold is asserted
        (would be flaky in CI) — only correctness."""
        n = 3000
        paths = []
        for i in range(n):
            p = write_tee(f"{i:06d}-" + ("x" * 50))
            t = time.time() - (n - i)  # strictly ascending mtimes
            os.utime(p, (t, t))
            paths.append(p)

        keep_count = 100
        budget = paths[0].stat().st_size * keep_count

        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=budget)

        survivors = list(tee_dir().glob("*.txt"))
        survivor_total = sum(p.stat().st_size for p in survivors)
        assert survivor_total <= budget
        assert paths[-1].exists()  # newest survives
        assert not paths[0].exists()  # clearly-oldest is gone


# ---------------------------------------------------------------------------
# current_tee_size_bytes (QB-103)
# ---------------------------------------------------------------------------


class TestCurrentTeeSizeBytes:
    def test_empty_dir_returns_zero(self) -> None:
        from quor.pipeline.tee import current_tee_size_bytes

        assert current_tee_size_bytes() == 0

    def test_sums_all_file_sizes(self) -> None:
        from quor.pipeline.tee import current_tee_size_bytes

        write_tee("a" * 100)
        write_tee("b" * 250)

        assert current_tee_size_bytes() == 350

    def test_does_not_delete_anything(self) -> None:
        from quor.pipeline.tee import current_tee_size_bytes

        path = write_tee("content")
        current_tee_size_bytes()
        assert path.exists()


# ---------------------------------------------------------------------------
# quor doctor — _check_tee_size (QB-103)
# ---------------------------------------------------------------------------


class TestCheckTeeSize:
    def test_reports_size_and_limit_within_budget(self) -> None:
        from quor.cli.commands.doctor import _check_tee_size
        from quor.config.model import QuorUserConfig

        path = write_tee("a" * 1000)
        user_config = QuorUserConfig(tee_max_bytes=1_000_000)

        name, ok, detail = _check_tee_size(user_config)

        expected_size_mb = path.stat().st_size / (1024 * 1024)
        expected_limit_mb = 1_000_000 / (1024 * 1024)
        assert name == "Tee cache size"
        assert ok is True
        assert detail == f"{expected_size_mb:.1f} MB used of {expected_limit_mb:.0f} MB limit"

    def test_reports_over_limit(self) -> None:
        from quor.cli.commands.doctor import _check_tee_size
        from quor.config.model import QuorUserConfig

        path = write_tee("a" * 10_000)
        user_config = QuorUserConfig(tee_max_bytes=100)

        name, ok, detail = _check_tee_size(user_config)

        expected_size_mb = path.stat().st_size / (1024 * 1024)
        expected_limit_mb = 100 / (1024 * 1024)
        assert name == "Tee cache size"
        assert ok is True  # advisory only — must never fail doctor
        assert detail == (
            f"{expected_size_mb:.1f} MB used, over the {expected_limit_mb:.0f} MB limit — "
            "will be trimmed on the next scheduled cleanup"
        )


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
