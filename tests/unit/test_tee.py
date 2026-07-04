"""Unit tests for quor/pipeline/tee.py — ADR-023 tee mechanism.

Isolated from real disk via the autouse `_isolate_platformdirs` fixture in
tests/conftest.py (patches platformdirs.user_data_dir to a per-test tmp dir).
"""

from __future__ import annotations

import os
import time

import pytest

from quor.pipeline.tee import cleanup_tee, content_hash, tee_dir, tee_path, write_tee

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
