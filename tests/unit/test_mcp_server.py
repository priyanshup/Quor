"""Unit tests for quor/mcp/server.py's compress_context/get_repo_context tools.

`@mcp.tool()` (the `mcp` SDK's MCPServer.tool() decorator) returns the
original plain function unchanged — verified directly against the
installed SDK before writing these — so compress_context is called here
exactly like any other function, no MCP protocol/transport mocking needed.

QB-089: compress_context's dedup cache (`quor.mcp.server._dedup_cache`) is
module-level, shared process-lifetime state by design (see
session_dedup.py's docstring for why that's correct for a real MCP server).
That makes it a test-isolation hazard here — the `_fresh_dedup_cache`
fixture below resets it before every test in this file so tests can't leak
state into each other.

QB-105: `quor.mcp.server._tracking_db` is the same kind of module-level,
process-lifetime singleton (see its own docstring for why it's lazy rather
than eager like `_dedup_cache`). `_fresh_tracking_db` below swaps in a
`tmp_path`-backed `TrackingDB` for the duration of each test — both for the
same leak-between-tests reason `_fresh_dedup_cache` exists, and because the
real `get_tracking_db()` would otherwise touch the actual platformdirs
`quor.db` on disk merely by running this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import quor.mcp.server as mcp_server
from quor.mcp.server import compress_context, get_repo_context
from quor.mcp.session_dedup import DEFAULT_CACHE_SIZE, SessionDedupCache
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.tracking.db import (
    MCP_DEDUP_FILTER_LABEL,
    MCP_REPO_CONTEXT_FILTER_LABEL,
    TrackingDB,
    count_tokens,
    query_recent_invocations,
)


@pytest.fixture(autouse=True)
def _fresh_dedup_cache() -> Iterator[None]:
    original = mcp_server._dedup_cache
    mcp_server._dedup_cache = SessionDedupCache()
    try:
        yield
    finally:
        mcp_server._dedup_cache = original


@pytest.fixture(autouse=True)
def _fresh_tracking_db(tmp_path: Path) -> Iterator[TrackingDB]:
    db = TrackingDB(db_path=tmp_path / "quor.db")
    original = mcp_server._tracking_db
    mcp_server._tracking_db = db
    try:
        yield db
    finally:
        db.close()
        mcp_server._tracking_db = original


def _recent_rows(tmp_path: Path):
    since = datetime.now(UTC) - timedelta(minutes=5)
    return query_recent_invocations(tmp_path / "quor.db", tmp_path, since=since)


class TestCompressContextBasics:
    def test_empty_input_returns_zero_percent_unchanged(self) -> None:
        result = compress_context("")
        assert result == "[Quor Compressed: 0% saved]\n"

    def test_first_call_compresses_normally(self) -> None:
        text = "\n".join(f"line {i}" for i in range(100))
        result = compress_context(text)
        assert result.startswith("[Quor Compressed:")
        assert "unchanged since last shown" not in result


class TestCompressContextDedup:
    def test_repeat_call_with_identical_text_returns_unchanged_marker(self) -> None:
        text = "\n".join(f"line {i}" for i in range(100))
        first = compress_context(text)
        second = compress_context(text)
        assert "unchanged since last shown this session" in second
        assert second != first

    def test_different_text_never_deduped(self) -> None:
        compress_context("some content A")
        result = compress_context("some content B, totally different")
        assert "unchanged since last shown" not in result

    def test_dedup_marker_never_larger_than_original_compression(self) -> None:
        """Net-expansion guardrail: the dedup path must always be at least
        as cheap as showing the content again, for any input size."""
        text = "\n".join(f"line {i}" for i in range(500))
        first = compress_context(text)
        second = compress_context(text)
        assert len(second) < len(first)

    def test_empty_input_bypasses_dedup_cache(self) -> None:
        """Empty input is handled by the token==0 early return before the
        dedup check — repeating it must not consume a cache slot or ever
        produce the dedup marker (there's nothing meaningful to dedup)."""
        first = compress_context("")
        second = compress_context("")
        assert first == second
        assert "unchanged since last shown" not in second

    def test_eviction_allows_recompression_after_window_fills(self) -> None:
        """Once DEFAULT_CACHE_SIZE other distinct calls have happened, an
        earlier hash falls out of the window and is compressed fresh again
        rather than deduped — the whole point of bounding the cache."""
        first_text = "the original content, shown once"
        compress_context(first_text)

        # Fill the window with enough distinct calls to evict first_text.
        for i in range(DEFAULT_CACHE_SIZE + 5):
            compress_context(f"filler content number {i}, unique every time")

        result = compress_context(first_text)
        assert "unchanged since last shown" not in result


class TestCompressContextTracking:
    """QB-105: compress_context must call track_invocation() so `quor gain`/
    `dashboard`/`doctor` reflect real MCP usage."""

    def test_empty_input_is_untracked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        compress_context("")
        # flush() (not just the fact nothing was tracked) matters here: the
        # fixture's TrackingDB creates its schema on a background thread, and
        # _recent_rows() connects to the same file independently — without
        # synchronizing on flush() first, the file can exist before the
        # `invocations` table does, racing query_recent_invocations().
        _fresh_tracking_db.flush()
        assert _recent_rows(tmp_path) == []

    def test_normal_compression_is_tracked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        text = "\n".join(f"line {i}" for i in range(100))
        result = compress_context(text)
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].command == "MCP compress_context"
        assert rows[0].filter_name == "generic"
        assert rows[0].original_tokens > 0
        # The tracked final_tokens must reflect exactly what the caller
        # received, not an intermediate value (QB-094's own principle).
        assert rows[0].final_tokens == count_tokens(result)

    def test_dedup_hit_is_tracked_under_its_own_label(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        text = "\n".join(f"line {i}" for i in range(100))
        compress_context(text)
        compress_context(text)  # second call hits the QB-089 dedup cache
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 2
        dedup_rows = [r for r in rows if r.filter_name == MCP_DEDUP_FILTER_LABEL]
        assert len(dedup_rows) == 1
        # The marker is tiny regardless of input size — a real, large savings.
        assert dedup_rows[0].final_tokens < dedup_rows[0].original_tokens

    def test_tracking_failure_does_not_affect_returned_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-open: a broken tracking DB must never surface to the caller."""
        monkeypatch.chdir(tmp_path)

        def _boom() -> TrackingDB:
            raise RuntimeError("tracking DB unavailable")

        monkeypatch.setattr(mcp_server, "_get_tracking_db", _boom)
        text = "\n".join(f"line {i}" for i in range(100))
        result = compress_context(text)
        assert result.startswith("[Quor Compressed:")


class TestGetRepoContextTracking:
    """QB-105: get_repo_context must call track_invocation() the same way
    quor map/explore/repo already do for their own synthesis-not-compression
    commands."""

    def _seed_intel(self, root: Path) -> None:
        intel_store.save_file_intelligence(
            root, {"a.py": FileIntelligenceEntry(language="python", kind="source")}
        )

    def test_no_intelligence_built_is_untracked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        get_repo_context(file_path="a.py")
        # See test_empty_input_is_untracked's own comment: flush() first to
        # avoid racing the fixture's TrackingDB's own background schema setup.
        _fresh_tracking_db.flush()
        assert _recent_rows(tmp_path) == []

    def test_successful_call_is_tracked_under_synthesis_label(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        self._seed_intel(tmp_path)

        get_repo_context(file_path="a.py")
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].command == "MCP get_repo_context: a.py"
        assert rows[0].filter_name == MCP_REPO_CONTEXT_FILTER_LABEL
        # Synthesis, not compression — no "before" blob, recorded equal.
        assert rows[0].original_tokens == rows[0].final_tokens

    def test_query_only_call_records_query_in_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        self._seed_intel(tmp_path)

        get_repo_context(query="foo")
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].command == "MCP get_repo_context: query='foo'"

    def test_no_args_call_is_still_tracked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        self._seed_intel(tmp_path)

        get_repo_context()
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].command == "MCP get_repo_context: (no args)"
