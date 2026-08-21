"""QB-123 (Zero-New-File Architecture): verification tests.

Three claims this ticket makes, each pinned here:
  1. Output compression (both the CLI dispatch path and the MCP
     compress_context path) creates zero new individual files on disk —
     recovery content lives entirely in SQLite rows now.
  2. Recovery entries stay within a bounded retention policy (age + size,
     inherited from the pre-existing QB-103 ceiling — see
     docs/design or tests/unit/test_tee.py for why a hard row-count cap was
     deliberately not adopted) rather than growing unboundedly.
  3. The startup orphan sweeper purges stale `quor_*` temp directories
     while leaving fresh ones alone.

Isolated from real disk via the autouse `_isolate_platformdirs` fixture in
tests/conftest.py.
"""

from __future__ import annotations

import io
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import platformdirs
import pytest

import quor.mcp.server as mcp_server
from quor.engine.dispatcher import run_dispatch
from quor.mcp.session_dedup import SessionDedupCache
from quor.pipeline.orphan_sweep import sweep_orphaned_temp_dirs
from quor.pipeline.tee import cleanup_tee, current_tee_size_bytes, write_tee
from quor.tracking.db import TrackingDB


def _make_proc(*, stdout: str, returncode: int = 1) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.returncode = returncode
    return proc


_REPEATED_FAILURE_OUTPUT = (
    "".join(f"PASSED tests/test_{i}.py::test_ok\n" for i in range(200))
    + "".join(f"FAILED tests/test_{i}.py::test_case\n" for i in range(20))
)

# Truly repetitive content (matches test_mcp_dispatcher_parity.py's own
# _COMPRESSIBLE_TEXT shape) — the generic filter's deduplicate_consecutive
# stage collapses this reliably, so tee is guaranteed to actually fire
# (captured != final), not just "no files either way regardless".
_COMPRESSIBLE_TEXT = ("INFO: heartbeat ok\n" * 300) + "ERROR: something distinct happened\n"


class TestZeroNewFilesOnCompression:
    @pytest.fixture(autouse=True)
    def _fresh_mcp_singletons(self, tmp_path: Path) -> Iterator[None]:
        """`quor.mcp.server`'s `_dedup_cache`/`_tracking_db` are real
        module-level, process-lifetime singletons — reset per test so a
        dedup-cache hit from another test file sharing this process doesn't
        suppress compress_context's tee call here (mirrors
        tests/unit/test_mcp_dispatcher_parity.py's own fixture)."""
        original_cache = mcp_server._dedup_cache
        mcp_server._dedup_cache = SessionDedupCache()
        db = TrackingDB(db_path=tmp_path / "quor.db")
        original_db = mcp_server._tracking_db
        mcp_server._tracking_db = db
        try:
            yield
        finally:
            db.close()
            mcp_server._tracking_db = original_db
            mcp_server._dedup_cache = original_cache

    def _data_dir_files(self) -> set[Path]:
        data_dir = Path(platformdirs.user_data_dir("quor"))
        if not data_dir.exists():
            return set()
        return {p for p in data_dir.rglob("*") if p.is_file()}

    def test_cli_dispatch_creates_no_individual_recovery_files(self) -> None:
        """A compressible command run through run_dispatch() (the same path
        `quor <cmd>` takes) must not create any per-invocation file — only
        rows inside the single tee_state.db."""
        before = self._data_dir_files()

        proc = _make_proc(stdout=_REPEATED_FAILURE_OUTPUT)
        with (
            patch("subprocess.run", return_value=proc),
            patch("sys.stdout", io.StringIO()),
        ):
            run_dispatch(["pytest", "tests/"])

        after = self._data_dir_files()
        new_files = after - before
        allowed_names = {
            "tee_state.db",
            "tee_state.db-wal",
            "tee_state.db-shm",
            "tee_state.db-journal",
            # Pre-existing, out-of-scope for QB-123: a single machine-wide
            # counter file (quor/pipeline/onboarding.py) rewritten in place
            # via atomic replace, not created per invocation.
            "onboarding_count.txt",
        }
        unexpected = {p for p in new_files if p.name not in allowed_names}
        assert unexpected == set()

    def test_mcp_compress_context_creates_no_individual_recovery_files(self) -> None:
        """Same guarantee over the MCP tool-call path (QB-114 parity) —
        compress_context() must not create a per-call file either."""
        from quor.mcp.server import compress_context

        before = self._data_dir_files()

        result = compress_context(_COMPRESSIBLE_TEXT)
        assert "[full output:" in result  # confirms tee actually fired

        after = self._data_dir_files()
        new_files = after - before
        allowed_names = {
            "tee_state.db",
            "tee_state.db-wal",
            "tee_state.db-shm",
            "tee_state.db-journal",
            "quor.db",
            "quor.db-wal",
            "quor.db-shm",
            "quor.db-journal",
        }
        unexpected = {p for p in new_files if p.name not in allowed_names}
        assert unexpected == set()

    def test_repeated_compressions_stay_at_one_state_db_each(self) -> None:
        """Many distinct compressible outputs must still land as SQLite
        rows, never as a growing count of files — the whole premise of
        QB-123 (per-file Defender scan / MFT fragmentation cost) only goes
        away if row count, not file count, is what scales with usage."""
        before = self._data_dir_files()

        proc_template = _REPEATED_FAILURE_OUTPUT
        for i in range(25):
            variant = proc_template + f"UNIQUE-{i}\n"
            proc = _make_proc(stdout=variant)
            with (
                patch("subprocess.run", return_value=proc),
                patch("sys.stdout", io.StringIO()),
            ):
                run_dispatch(["pytest", "tests/"])

        after = self._data_dir_files()
        new_files = after - before
        # Regardless of how many distinct outputs were teed, the file *count*
        # never grows — only the one state db (+ its WAL/SHM sidecars) and
        # the single onboarding counter file (see the allowlist comment in
        # test_cli_dispatch_creates_no_individual_recovery_files above).
        assert len(new_files) <= 5


class TestBoundedRetentionNotUnbounded:
    def test_cleanup_evicts_beyond_size_ceiling(self) -> None:
        """Recovery rows respect the same bounded age/size ceiling tee
        already enforced pre-QB-123 (QB-103) — writing well past the budget
        must not leave the cache to grow without limit."""
        for i in range(50):
            write_tee(f"entry-{i}-" + ("x" * 200))

        size_before_cleanup = current_tee_size_bytes()
        budget = size_before_cleanup // 5  # force heavy eviction

        cleanup_tee(max_age_days=7, throttle_hours=0, max_bytes=budget)

        assert current_tee_size_bytes() <= budget

    def test_cleanup_evicts_beyond_age_ceiling(self) -> None:
        """Old entries are dropped by the age window independent of size —
        the retention policy is never "keep everything forever"."""
        import sqlite3

        digest = write_tee("aging content")
        state_path = Path(platformdirs.user_data_dir("quor")) / "tee_state.db"
        conn = sqlite3.connect(str(state_path))
        try:
            stale = time.time() - (8 * 86400)
            conn.execute(
                "UPDATE tee_logs SET last_used_at = ? WHERE content_hash = ?", (stale, digest)
            )
            conn.commit()
        finally:
            conn.close()

        cleanup_tee(max_age_days=7, throttle_hours=0)

        from quor.pipeline.tee import read_tee

        assert read_tee(digest) is None


class TestOrphanSweeper:
    def test_purges_stale_quor_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import tempfile as tempfile_module

        temp_root = tmp_path / "fake_os_temp"
        temp_root.mkdir()
        monkeypatch.setattr(tempfile_module, "gettempdir", lambda: str(temp_root))

        stale_dir = temp_root / "quor_stale_12345"
        stale_dir.mkdir()
        (stale_dir / "leftover.txt").write_text("orphaned", encoding="utf-8")
        two_hours_ago = time.time() - 7200
        import os

        os.utime(stale_dir, (two_hours_ago, two_hours_ago))

        sweep_orphaned_temp_dirs()

        assert not stale_dir.exists()

    def test_keeps_fresh_quor_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import tempfile as tempfile_module

        temp_root = tmp_path / "fake_os_temp"
        temp_root.mkdir()
        monkeypatch.setattr(tempfile_module, "gettempdir", lambda: str(temp_root))

        fresh_dir = temp_root / "quor_active_67890"
        fresh_dir.mkdir()

        sweep_orphaned_temp_dirs()

        assert fresh_dir.exists()

    def test_ignores_non_quor_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import os
        import tempfile as tempfile_module

        temp_root = tmp_path / "fake_os_temp"
        temp_root.mkdir()
        monkeypatch.setattr(tempfile_module, "gettempdir", lambda: str(temp_root))

        other_dir = temp_root / "somethingelse_12345"
        other_dir.mkdir()
        two_hours_ago = time.time() - 7200
        os.utime(other_dir, (two_hours_ago, two_hours_ago))

        sweep_orphaned_temp_dirs()

        assert other_dir.exists()

    def test_missing_temp_dir_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tempfile as tempfile_module

        monkeypatch.setattr(
            tempfile_module, "gettempdir", lambda: str(tmp_path / "does_not_exist")
        )

        sweep_orphaned_temp_dirs()  # must not raise
