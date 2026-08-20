"""QB-114 (MCP Dispatcher Parity): before this ticket, `quor/mcp/server.py`
called `FilterRegistry.find()`/`.apply()` directly and skipped every other
safety net `quor/engine/dispatcher.py`'s CLI path (`run_dispatch()`) runs
unconditionally — `write_tee()` (QB-013's recovery-link generation) and
`scan_for_secrets()` (QB-029/PA-F07) never fired for an MCP tool call, only
for a command run through `quor <cmd>`.

`quor.engine.dispatcher.apply_filter_pipeline()` closes that gap: both
`compress_context` and `run_dispatch()` now call the exact same private
helpers (`_lookup_filter`, `_apply_content_filter`, `_apply_tee`,
`_scan_secrets_safe`, etc.), not two parallel implementations — see that
function's and the module's own docstrings. This file verifies the MCP
side of that parity specifically; `tests/unit/test_adapters.py` already
covers those helpers' own behavior in depth via the CLI path.

Fixture pattern (`_fresh_dedup_cache`/`_fresh_tracking_db`) copied from
`test_mcp_server.py`'s own docstring rationale: both are real module-level,
process-lifetime singletons in `quor.mcp.server`, and must be reset per
test so state can't leak between tests in this file.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import quor.mcp.server as mcp_server
from quor.engine import dispatcher
from quor.mcp.server import compress_context, get_repo_context
from quor.mcp.session_dedup import SessionDedupCache
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.pipeline.tee import tee_path
from quor.tracking.db import TrackingDB

_FAKE_GITHUB_TOKEN = "ghp_" + "a" * 36

# Enough repetition that the generic filter's deduplicate_consecutive stage
# collapses it to a handful of bytes — comfortably clears _apply_tee's own
# QB-052 "footer must never cost more than the filter saved" budget gate
# regardless of how deep the real platformdirs tee path on this machine is.
_COMPRESSIBLE_TEXT = ("INFO: heartbeat ok\n" * 300) + "ERROR: something distinct happened\n"


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


class TestWriteTeeFiresOverMcp:
    """Requirement 2: write_tee() must fire on compress_context invocations
    so recovery links are generated and accessible."""

    def test_recovery_footer_and_file_appear(self) -> None:
        result = compress_context(_COMPRESSIBLE_TEXT)

        assert "[full output:" in result
        match = re.search(r"\[full output: (.+)\]", result)
        assert match is not None
        recovered = Path(match.group(1))
        assert recovered.exists()
        # The recovered file holds the true raw text — including the 300
        # repeated INFO lines the generic filter's dedup stage collapsed
        # out of what compress_context actually returned.
        assert recovered.read_text(encoding="utf-8") == _COMPRESSIBLE_TEXT

    def test_recovery_file_matches_tee_paths_own_naming(self) -> None:
        """Same content-addressed path `_apply_tee()`/`write_tee()` already
        use for the CLI path (tests/unit/test_adapters.py's own
        TestDispatcherTee asserts this identically against run_dispatch) —
        proof this is the real write_tee(), not a lookalike."""
        result = compress_context(_COMPRESSIBLE_TEXT)

        expected_path = tee_path(_COMPRESSIBLE_TEXT)
        assert f"[full output: {expected_path}]" in result
        assert expected_path.exists()

    def test_no_footer_when_input_too_small_to_compress(self) -> None:
        """Mirrors run_dispatch()'s own contract: tee only fires when
        filtering actually changed the content — nothing to recover
        otherwise. A short, already-clean line the generic filter leaves
        untouched must not grow a recovery footer."""
        result = compress_context("a single short clean line")
        assert "[full output:" not in result


class TestScanForSecretsFiresOverMcp:
    """Requirement 2/4: scan_for_secrets() must process MCP tool inputs and
    outputs before they cross the transport — a secret surviving into a
    tool's return value must trigger the same stderr warning
    run_dispatch() already emits for CLI output (PA-F07/QB-029)."""

    def test_compress_context_warns_on_leaked_token(self) -> None:
        text = f"AssertionError: token {_FAKE_GITHUB_TOKEN} leaked in response\n"
        with pytest.warns(UserWarning, match="Possible secret detected"):
            result = compress_context(text)

        # Detection only — never redacted, matches _scan_secrets_safe's own
        # documented contract on the CLI path.
        assert _FAKE_GITHUB_TOKEN in result

    def test_compress_context_no_warning_without_a_secret(self) -> None:
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compress_context(_COMPRESSIBLE_TEXT)

        assert not any("secret" in str(w.message).lower() for w in caught)

    def test_get_repo_context_warns_when_query_echoes_a_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_repo_context never runs the ContentMask/tee pipeline (its
        output is synthesized metadata, not captured command output — see
        quor/mcp/server.py's module docstring) but must still get the
        secret-scan safety net on its result before returning."""
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(
            tmp_path, {"a.py": FileIntelligenceEntry(language="python", kind="source")}
        )

        with pytest.warns(UserWarning, match="Possible secret detected"):
            result = get_repo_context(query=_FAKE_GITHUB_TOKEN)

        assert _FAKE_GITHUB_TOKEN in result

    def test_get_repo_context_no_intelligence_bailout_is_scanned_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even the early "run `quor map` first" bailout path — the only
        other return point in get_repo_context — goes through the same
        scan call. No secret-shaped content reaches it in practice, so this
        just proves the call site runs without raising, not that a warning
        fires."""
        monkeypatch.chdir(tmp_path)
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = get_repo_context(file_path="a.py")

        assert "run `quor map`" in result
        assert not any("secret" in str(w.message).lower() for w in caught)


class TestStdoutStaysClean:
    """Requirement 3: dispatcher logs, secret warnings, and recovery
    metadata must write exclusively to stderr/the log file — stdout must
    stay 100% clean for MCP's stdio JSON-RPC transport. compress_context/
    get_repo_context are plain functions here (no real stdio transport in
    a unit test), so this proves the property one level down: nothing
    the dispatcher pipeline does along the way ever touches sys.stdout."""

    def test_compress_context_writes_nothing_to_stdout(self) -> None:
        captured = io.StringIO()
        text = f"AssertionError: token {_FAKE_GITHUB_TOKEN} leaked\n"
        with contextlib.redirect_stdout(captured), pytest.warns(UserWarning):
            compress_context(text)

        assert captured.getvalue() == ""

    def test_get_repo_context_writes_nothing_to_stdout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        intel_store.save_file_intelligence(
            tmp_path, {"a.py": FileIntelligenceEntry(language="python", kind="source")}
        )

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            get_repo_context(file_path="a.py")

        assert captured.getvalue() == ""


class TestApplyFilterPipelineDirect:
    """Direct coverage of quor.engine.dispatcher.apply_filter_pipeline()
    itself — the shared function both run_dispatch() (CLI) and
    compress_context (MCP) now call, verified independent of MCP's own
    dedup/tracking wrapping above."""

    def test_returns_filter_config_and_tees_on_compressible_content(self) -> None:
        output, filter_config = dispatcher.apply_filter_pipeline(
            _COMPRESSIBLE_TEXT, _COMPRESSIBLE_TEXT
        )

        assert filter_config is not None
        assert filter_config.name == "generic"
        assert "[full output:" in output

    def test_no_op_match_leaves_content_byte_identical(self) -> None:
        """A true `filter_config is None` passthrough is effectively
        unreachable for a content-only caller — the generic catch-all's
        `match_command = '.'` always matches (see FilterRegistry.find()'s
        own docstring and quor/mcp/server.py's module docstring for why).
        The no-op case that matters here is content the generic filter's
        stages leave untouched — proves the pipeline doesn't invent
        changes on already-clean input."""
        text = "a single short clean line"
        output, filter_config = dispatcher.apply_filter_pipeline(text, text)

        assert filter_config is not None
        assert filter_config.name == "generic"
        assert output == text

    def test_never_writes_to_stdout(self) -> None:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            dispatcher.apply_filter_pipeline(_COMPRESSIBLE_TEXT, _COMPRESSIBLE_TEXT)

        assert captured.getvalue() == ""


class TestRealStdioToolCall:
    """End-to-end proof of requirement 4's "tool responses remain correctly
    formatted JSON-RPC without stdout pollution": spawns the real
    quor.mcp.launcher subprocess and calls compress_context over a real
    stdio transport, the same path any MCP client uses. If dispatcher's
    pipeline had leaked anything onto stdout (a print, an unflushed log
    line), the client-side JSON-RPC framing would fail to parse or the
    call would hang — this either gets back a clean, well-formed
    CallToolResult or the test fails via the timeout/exception, not a
    silent false pass. Marked integration (real subprocess, real stdio I/O)
    per this project's existing convention — see tests/unit/test_doctor.py's
    own real-handshake test for the same pattern."""

    @pytest.mark.integration
    def test_compress_context_over_real_stdio_transport(self) -> None:
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def _call() -> tuple[bool, str]:
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "quor.mcp.launcher"],
                cwd=str(Path.cwd()),
                env={"QUOR_MCP_DISABLE_AUTOREPAIR": "1"},
            )
            with anyio.fail_after(20.0):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "compress_context", {"raw_text": _COMPRESSIBLE_TEXT}
                        )
                        text = "".join(
                            getattr(block, "text", "") for block in result.content
                        )
                        return result.is_error, text

        is_error, text = anyio.run(_call)

        assert is_error is False
        assert text.startswith("[Quor Compressed:")
        assert "[full output:" in text
