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

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import platformdirs
import pytest

import quor.mcp.server as mcp_server
from quor.mcp.schema_pruner import DEFAULT_MAX_TOOL_DESCRIPTION_CHARS
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


def _latest_files_changed(tmp_path: Path) -> int | None:
    # QB-109: files_changed isn't exposed on RecentInvocation (that type is
    # metadata for quor dashboard's feed, not a full row) — query the column
    # directly, the same way the real-DB audit that found the QB-093
    # telemetry gap did.
    conn = sqlite3.connect(tmp_path / "quor.db")
    try:
        row = conn.execute(
            "SELECT files_changed FROM invocations ORDER BY recorded_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


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

    def test_git_diff_content_records_files_changed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        # QB-109: files_changed (QB-093 telemetry prep) must actually
        # populate through the real MCP path now that match_content_types
        # lets git-diff be selected here — this is the exact gap a direct
        # `quor.db` audit found (358 real git-diff rows, 0 with the column
        # set, because it was only ever wired into dispatcher.py's dead-for-
        # real-usage git-diff call site).
        monkeypatch.chdir(tmp_path)
        diff_text = (
            "diff --git a/foo.py b/foo.py\n"
            "index abc123..def456 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " context\n"
            "+new line\n"
            "diff --git a/bar.py b/bar.py\n"
            "index 111..222 100644\n"
            "--- a/bar.py\n"
            "+++ b/bar.py\n"
            "@@ -1,2 +1,3 @@\n"
            " context\n"
            "+another new line\n"
        )
        compress_context(diff_text)
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].filter_name == "git-diff"
        assert _latest_files_changed(tmp_path) == 2

    def test_non_diff_content_leaves_files_changed_null(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)
        text = "\n".join(f"line {i}" for i in range(100))
        compress_context(text)
        _fresh_tracking_db.flush()
        assert _latest_files_changed(tmp_path) is None

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


class TestCompressContextFilePathRouting:
    """QB-133 follow-on: compress_context's lightweight `file_path` hint —
    unlike `focal_file`, it needs no `quor map` and never triggers
    graph-distance tiering, it only lets `raw_text` be routed to the
    matching language/format-specific filter instead of always falling
    through to `generic`. This is what makes the QB-133 filter-routing fix
    actually reach the tool's primary, no-repo-intelligence-required usage
    pattern, not just the heavier `focal_file` one."""

    _PY_SOURCE = 'def foo(x, y):\n    """Add two numbers."""\n    total = x + y\n    return total\n'

    def test_file_path_routes_to_language_specific_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        result = compress_context(self._PY_SOURCE, file_path="sample.py")

        assert "total = x + y" not in result  # AST-compressed, not the generic passthrough

    def test_without_file_path_same_content_stays_generic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        """Regression guard, pinning the bug this fixes: the exact same
        Python-shaped content, with no `file_path` given, has no way to be
        routed to `cat-python` — exactly as it did before this feature."""
        monkeypatch.chdir(tmp_path)

        compress_context(self._PY_SOURCE)
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].filter_name == "generic"

    def test_focal_file_takes_priority_over_file_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When both are given, `focal_file` (the stronger, repository-
        intelligence-backed identity) wins — `file_path` is simply never
        consulted, mirroring `compress_context()`'s own early return."""
        monkeypatch.chdir(tmp_path)

        result = compress_context(
            self._PY_SOURCE, focal_file="missing.py", file_path="sample.py"
        )

        assert "No repository intelligence" in result

    def test_file_path_outside_cwd_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        """The same in-repo path-traversal guard `focal_file`/`get_repo_context`
        already apply — an out-of-repo `file_path` must be silently ignored,
        not raise, and not leak a filter selection from outside the project."""
        monkeypatch.chdir(tmp_path)

        result = compress_context(self._PY_SOURCE, file_path="../outside/sample.py")
        _fresh_tracking_db.flush()

        assert result.startswith("[Quor Compressed:")
        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].filter_name == "generic"
        assert rows[0].command == "MCP compress_context"

    def test_tracked_command_records_the_file_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        monkeypatch.chdir(tmp_path)

        compress_context(self._PY_SOURCE, file_path="src/sample.py")
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].command == "MCP compress_context: file_path=src/sample.py"
        assert rows[0].filter_name == "cat-python"

    def test_exclude_patterns_bypass_applies_when_file_path_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fresh_tracking_db: TrackingDB
    ) -> None:
        """QB-130's exclude_patterns only ever applies when a real file
        identity is available — `file_path` now supplies that for the plain
        `raw_text` call shape too, resolved from a `.quor.toml` found
        relative to `file_path` itself, not just cwd."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".quor.toml").write_text(
            "[ignore]\nexclude_patterns = [\"*.py\"]\n", encoding="utf-8"
        )

        compress_context(self._PY_SOURCE, file_path="sample.py")
        _fresh_tracking_db.flush()

        rows = _recent_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].filter_name is None  # bypassed, same as "no filter matched"


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


class TestSchemaPruning:
    """QB-120: `mcp.list_tools()` is wrapped with `schema_pruner.
    prune_tool_schema()` (see `_pruned_list_tools` in server.py); `mcp.
    call_tool()` is not — it resolves through `_tool_manager.call_tool()`
    directly, a path `list_tools()` never touches. In-process calls
    against the real `mcp` object, no transport/subprocess needed — same
    reasoning as this file's own module docstring for why that's safe."""

    def test_list_tools_condenses_verbose_descriptions(self) -> None:
        import anyio

        tools = anyio.run(mcp_server.mcp.list_tools)
        by_name = {t.name: t for t in tools}
        assert "compress_context" in by_name
        assert "get_repo_context" in by_name
        for tool in tools:
            assert tool.description is not None
            assert len(tool.description) <= DEFAULT_MAX_TOOL_DESCRIPTION_CHARS

    def test_call_tool_still_resolves_full_arguments(self) -> None:
        import anyio

        async def _call() -> object:
            return await mcp_server.mcp.call_tool("compress_context", {"raw_text": ""})

        result = anyio.run(_call)
        assert getattr(result, "is_error", False) is False


# ---------------------------------------------------------------------------
# QB-136: MCP Resources (quor://metrics/gain, quor://config/effective) and
# the compress_file_prompt Prompt. In-process, same reasoning as this file's
# own module docstring for why that's safe for tools: `@mcp.resource()`/
# `@mcp.prompt()` also return the original plain function unchanged.
# ---------------------------------------------------------------------------


class TestMetricsGainResource:
    """quor://metrics/gain — must byte-for-byte match `quor gain --format
    json`'s own payload shape (it reuses the exact same
    build_gain_payload()/render_gain_json() serializers), and must never
    raise regardless of what goes wrong underneath."""

    def test_empty_project_is_clean_zeroed_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No quor.db at all yet (query_gain()'s own fail-open) — a plain
        empty/zeroed summary, not an error."""
        monkeypatch.chdir(tmp_path)

        body = mcp_server.metrics_gain_resource()
        payload = orjson.loads(body)

        assert payload["summary"]["total_operations"] == 0
        assert payload["summary"]["tokens_saved"] == 0
        assert "error" not in payload

    def test_reflects_a_real_recorded_invocation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Writes directly to the same on-disk path
        `platformdirs.user_data_dir("quor")/quor.db` resolves to (the exact
        path `quor gain`/`get_tracking_db()` themselves use) rather than
        going through the module-level `_tracking_db` singleton this file's
        other fixtures swap out — proving this resource reads the real,
        conventional location, not a test-only shortcut."""
        from quor.tracking.db import InvocationRecord, TrackingDB

        monkeypatch.chdir(tmp_path)
        data_dir = tmp_path / "data"
        monkeypatch.setattr(platformdirs, "user_data_dir", lambda *_a, **_kw: str(data_dir))

        db = TrackingDB(db_path=data_dir / "quor.db")
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

        body = mcp_server.metrics_gain_resource()
        payload = orjson.loads(body)

        assert payload["summary"]["total_operations"] == 1
        assert payload["summary"]["tokens_saved"] == 80

    def test_fails_open_as_json_error_on_unexpected_query_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_kw: object) -> None:
            raise sqlite3.Error("database is locked")

        monkeypatch.setattr(mcp_server, "query_gain", _boom)

        body = mcp_server.metrics_gain_resource()
        payload = orjson.loads(body)

        assert "error" in payload
        assert "database is locked" in payload["error"]


class TestConfigEffectiveResource:
    """quor://config/effective — global + project + merged-effective view of
    QB-130's resolve_effective_config(), plus its own fail-open contract."""

    def test_no_project_config_reports_project_as_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        body = mcp_server.config_effective_resource()
        payload = orjson.loads(body)

        assert payload["project"] is None
        assert payload["global"] == payload["effective"]
        assert payload["global"]["min_token_threshold"] == 0

    def test_project_override_is_shown_both_raw_and_merged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".quor.toml").write_text(
            "[compression]\nmin_token_threshold = 500\n", encoding="utf-8"
        )

        body = mcp_server.config_effective_resource()
        payload = orjson.loads(body)

        assert payload["project"]["compression"]["min_token_threshold"] == 500
        assert payload["global"]["min_token_threshold"] == 0  # untouched
        assert payload["effective"]["min_token_threshold"] == 500  # merged

    def test_fails_open_as_json_error_on_invalid_project_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """find_and_load_project_config() raises ConfigError on malformed
        TOML (fail-loud, by design, for its own direct callers) — this
        resource still must not let that exception cross the transport
        boundary, unlike `_resolve_project_overrides()`'s silent fallback
        (see this resource's own docstring for why it reports instead of
        hiding the failure)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".quor.toml").write_text("not valid toml [[[", encoding="utf-8")

        body = mcp_server.config_effective_resource()
        payload = orjson.loads(body)

        assert "error" in payload


class TestCompressFilePromptResource:
    """compress_file_prompt — routes through the identical
    apply_filter_pipeline() call compress_context(file_path=...) uses, so
    the returned prompt embeds genuinely compressed content, not the raw
    file."""

    _PY_SOURCE = 'def foo(x, y):\n    """Add two numbers."""\n    total = x + y\n    return total\n'

    def test_embeds_compressed_content_for_a_real_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sample.py").write_text(self._PY_SOURCE, encoding="utf-8")

        result = mcp_server.compress_file_prompt("sample.py")

        assert "Analyze the following compressed content of sample.py" in result
        assert "filter: cat-python" in result
        assert "total = x + y" not in result  # AST-compressed, not the raw file

    def test_missing_file_returns_explanatory_string_not_a_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        result = mcp_server.compress_file_prompt("does_not_exist.py")

        assert "Could not read" in result

    def test_path_outside_cwd_is_rejected_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        result = mcp_server.compress_file_prompt("../outside/sample.py")

        assert "Could not resolve" in result

    def test_registered_as_an_mcp_prompt(self) -> None:
        import anyio

        prompts = anyio.run(mcp_server.mcp.list_prompts)
        by_name = {p.name: p for p in prompts}

        assert "compress_file_prompt" in by_name
        assert "file_path" in {arg.name for arg in by_name["compress_file_prompt"].arguments or []}
