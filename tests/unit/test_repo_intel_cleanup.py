"""Unit tests for quor/pipeline/repo_profile/intel_cleanup.py (QB-124).

Isolated from real disk via the autouse `_isolate_platformdirs` fixture in
tests/conftest.py (patches platformdirs.user_data_dir to a per-test tmp dir),
same convention as tests/unit/test_tee.py.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import platformdirs

from quor.pipeline.repo_profile import intel_cleanup, intel_store


def _make_repo_dir(root: Path, name: str) -> Path:
    """A fake `<repo_key>` cache directory with a couple of files inside,
    mirroring what intel_store.py actually writes (state.json, profile.json,
    ...) without needing real RepoIntelState/RepoProfile objects."""
    cache_root = intel_store.cache_root()
    repo_dir = cache_root / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "state.json").write_text("{}", encoding="utf-8")
    (repo_dir / "profile.json").write_text("{}", encoding="utf-8")
    return repo_dir


def _touch_all(repo_dir: Path, when: float) -> None:
    for path in repo_dir.iterdir():
        os.utime(path, (when, when))


def _state_db_path() -> Path:
    return Path(platformdirs.user_data_dir("quor")) / "tee_state.db"


class TestCurrentRepoIntelSizeBytes:
    def test_zero_when_nothing_cached(self) -> None:
        assert intel_cleanup.current_repo_intel_size_bytes() == 0

    def test_sums_bytes_across_repos(self, tmp_path: Path) -> None:
        _make_repo_dir(tmp_path, "aaaa")
        _make_repo_dir(tmp_path, "bbbb")
        assert intel_cleanup.current_repo_intel_size_bytes() == 4 * len("{}")


class TestCleanupRepoIntelAgeEviction:
    def test_stale_repo_directory_is_removed(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path, "stale")
        old = time.time() - 40 * 86400  # 40 days old, past the 30-day default
        _touch_all(repo_dir, old)

        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=0)

        assert not repo_dir.exists()

    def test_fresh_repo_directory_survives(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path, "fresh")

        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=0)

        assert repo_dir.exists()

    def test_freshness_uses_newest_file_in_directory(self, tmp_path: Path) -> None:
        """A directory where one file is old but another was just rewritten
        (e.g. file_intelligence.json updated on an incremental refresh, while
        state.json's own mtime is older) must survive — see module docstring
        on why freshness is directory-wide, not per-file."""
        repo_dir = _make_repo_dir(tmp_path, "mixed")
        old = time.time() - 40 * 86400
        os.utime(repo_dir / "state.json", (old, old))
        # profile.json keeps its just-created (fresh) mtime.

        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=0)

        assert repo_dir.exists()

    def test_eviction_removes_the_whole_directory_not_partial_files(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path, "stale")
        old = time.time() - 40 * 86400
        _touch_all(repo_dir, old)

        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=0)

        assert not (repo_dir / "state.json").exists()
        assert not (repo_dir / "profile.json").exists()
        assert not repo_dir.exists()


class TestCleanupRepoIntelSizeEviction:
    def test_oldest_repo_evicted_first_once_over_budget(self, tmp_path: Path) -> None:
        older = _make_repo_dir(tmp_path, "older")
        newer = _make_repo_dir(tmp_path, "newer")
        now = time.time()
        _touch_all(older, now - 1000)
        _touch_all(newer, now)

        entry_size = len("{}")
        two_dirs_bytes = 2 * (2 * entry_size)
        # Budget fits one repo's worth of files but not both.
        budget = two_dirs_bytes - 1

        intel_cleanup.cleanup_repo_intel(max_age_days=365, throttle_hours=0, max_bytes=budget)

        assert not older.exists()
        assert newer.exists()

    def test_no_eviction_when_under_budget(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path, "small")

        intel_cleanup.cleanup_repo_intel(
            max_age_days=365, throttle_hours=0, max_bytes=1024 * 1024
        )

        assert repo_dir.exists()


class TestCleanupRepoIntelThrottle:
    def test_second_call_within_window_is_a_noop(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path, "stale")
        old = time.time() - 40 * 86400
        _touch_all(repo_dir, old)

        # First call (throttle_hours=0) runs the sweep and records last_cleanup_at now.
        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=0)
        assert not repo_dir.exists()

        # Recreate a stale directory, then call again with a real throttle window —
        # this call must be a no-op since the first call just ran.
        repo_dir = _make_repo_dir(tmp_path, "stale")
        _touch_all(repo_dir, old)
        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=24)

        assert repo_dir.exists()

    def test_throttle_state_lives_in_tee_state_db_not_a_new_file(self, tmp_path: Path) -> None:
        intel_cleanup.cleanup_repo_intel(throttle_hours=0)

        state_path = _state_db_path()
        assert state_path.exists()

        conn = sqlite3.connect(str(state_path))
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "repo_intel_cleanup" in names

    def test_past_throttle_window_runs_again(self, tmp_path: Path) -> None:
        repo_dir = _make_repo_dir(tmp_path, "stale")
        old = time.time() - 40 * 86400
        _touch_all(repo_dir, old)

        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=0)
        repo_dir = _make_repo_dir(tmp_path, "stale2")
        _touch_all(repo_dir, old)

        # Force last_cleanup_at far enough in the past to clear a 24h throttle.
        state_path = _state_db_path()
        conn = sqlite3.connect(str(state_path))
        try:
            stale_ts = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
            conn.execute(
                "UPDATE repo_intel_cleanup SET last_cleanup_at = ? WHERE id = 1", (stale_ts,)
            )
            conn.commit()
        finally:
            conn.close()

        intel_cleanup.cleanup_repo_intel(max_age_days=30, throttle_hours=24)

        assert not repo_dir.exists()


class TestCleanupRepoIntelNoDirectory:
    def test_noop_when_cache_root_never_created(self) -> None:
        # No repo has ever been mapped in this test's isolated environment —
        # must not raise.
        intel_cleanup.cleanup_repo_intel(throttle_hours=0)


# ---------------------------------------------------------------------------
# quor doctor — _check_repo_intel_size (QB-124), mirrors test_tee.py's
# TestCheckTeeSize for the tee counterpart.
# ---------------------------------------------------------------------------


class TestCheckRepoIntelSize:
    def test_reports_size_and_limit_within_budget(self, tmp_path: Path) -> None:
        from quor.cli.commands.doctor import Status, _check_repo_intel_size
        from quor.config.model import QuorUserConfig

        _make_repo_dir(tmp_path, "aaaa")
        user_config = QuorUserConfig(repo_intel_max_bytes=1_000_000)

        name, status, detail = _check_repo_intel_size(user_config)

        size_bytes = 2 * len("{}")
        expected_size_mb = size_bytes / (1024 * 1024)
        expected_limit_mb = 1_000_000 / (1024 * 1024)
        assert name == "Repository intelligence cache size"
        assert status is Status.PASS
        assert detail == f"{expected_size_mb:.1f} MB used of {expected_limit_mb:.0f} MB limit"

    def test_reports_over_limit(self, tmp_path: Path) -> None:
        from quor.cli.commands.doctor import Status, _check_repo_intel_size
        from quor.config.model import QuorUserConfig

        _make_repo_dir(tmp_path, "aaaa")
        user_config = QuorUserConfig(repo_intel_max_bytes=1)

        name, status, detail = _check_repo_intel_size(user_config)

        size_bytes = 2 * len("{}")
        expected_size_mb = size_bytes / (1024 * 1024)
        expected_limit_mb = 1 / (1024 * 1024)
        assert name == "Repository intelligence cache size"
        assert status is Status.WARN  # advisory only — must never fail doctor
        assert detail == (
            f"{expected_size_mb:.1f} MB used, over the {expected_limit_mb:.0f} MB limit — "
            "will be trimmed on the next `quor map`/`symbols`/`graph` cleanup pass"
        )
