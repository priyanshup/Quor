"""End-to-end MCP protocol tests (QB-129): spawn `quor/mcp/server.py` as a
real subprocess and speak real stdio JSON-RPC to it via the official `mcp`
client SDK (`mcp.client.stdio.stdio_client` + `mcp.ClientSession`) — the
exact transport/framing a real MCP client (Claude Code) uses, not a
same-process import of `quor.mcp.server`'s functions.

`@pytest.mark.integration` on every test here, matching this repo's one
existing real-stdio-subprocess precedent
(tests/unit/test_mcp_dispatcher_parity.py::test_compress_context_over_real_stdio_transport):
a real subprocess spawn + handshake costs real wall-clock seconds, and
pyproject.toml's `addopts = "... -m \"not integration\""` deliberately
excludes this class of test from the default `pytest`/`pytest tests/` run
(PA-Q04: default run stays under ~30s) — run these explicitly with
`pytest tests/e2e/test_mcp_e2e.py -m integration` (a bare `pytest
tests/e2e/test_mcp_e2e.py` inherits addopts' `-m "not integration"` and
collects but skips/deselects every test in this file, which would look
like a false pass otherwise).

Isolation: platformdirs' own env-var overrides
(`WIN_PD_OVERRIDE_LOCAL_APPDATA` on Windows, `XDG_DATA_HOME` elsewhere) are
set on both this test process (to resolve the same `quor.db`/`tee_state.db`
paths the child will use, for inspection afterward) and passed into the
spawned subprocess's own environment — the autouse `_isolate_platformdirs`
fixture in tests/conftest.py only patches `platformdirs.user_data_dir`
in-process, which a child subprocess started via `stdio_client()` never
sees. Every test gets its own fresh `tmp_path`, so no test's telemetry can
be mistaken for another's.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import platformdirs
import pytest

pytestmark = pytest.mark.integration

_HANDSHAKE_TIMEOUT_SECONDS = 20.0
_TELEMETRY_POLL_TIMEOUT_SECONDS = 5.0


def _isolated_data_env(data_dir: Path) -> dict[str, str]:
    """The env var(s) that redirect platformdirs' `user_data_dir()` to
    `data_dir`, for whichever OS this test is actually running on."""
    if sys.platform == "win32":
        return {"WIN_PD_OVERRIDE_LOCAL_APPDATA": str(data_dir)}
    return {"XDG_DATA_HOME": str(data_dir)}


@pytest.fixture
def isolated_quor_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Points the about-to-be-spawned subprocess's `platformdirs.
    user_data_dir("quor")` at a fresh `tmp_path`-backed directory (via env
    var, since the subprocess is a separate process this test can't
    monkeypatch directly), then returns the resulting `quor.db` path.

    Deliberately does NOT call `platformdirs.user_data_dir()` to compute
    that path — tests/conftest.py's autouse `_isolate_platformdirs` fixture
    monkeypatches that exact free function, in this same process, to always
    return a fixed per-test dir regardless of arguments or env vars, so it
    can't observe the override below at all. Instantiating `PlatformDirs`
    directly bypasses that monkeypatch (only the free function is patched,
    not the class it wraps) and reproduces the free function's own default
    args, so this resolves to the exact same path the *subprocess* (which
    never had `platformdirs.user_data_dir` monkeypatched — it's a separate
    process) computes for itself.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for key, value in _isolated_data_env(data_dir).items():
        monkeypatch.setenv(key, value)
    return Path(platformdirs.PlatformDirs(appname="quor").user_data_dir) / "quor.db"


def _server_params(data_dir: Path):
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "quor.mcp.server"],
        cwd=str(Path.cwd()),
        env=_isolated_data_env(data_dir),
    )


def _count_invocations(db_path: Path) -> int:
    """Standalone re-implementation of quor.tracking.db.count_invocations()
    rather than importing it — this test asserts against `quor.db` as an
    outside observer would (a plain SQLite read), independent of whether
    the module under test's own read helper has a bug."""
    import sqlite3

    if not db_path.exists():
        return 0
    with sqlite3.connect(str(db_path)) as conn:
        try:
            row = conn.execute("SELECT COUNT(*) FROM invocations").fetchone()
        except sqlite3.OperationalError:
            return 0  # table not created yet
    return int(row[0])


def _wait_for_invocation_count_at_least(db_path: Path, minimum: int) -> int:
    """Poll rather than assert immediately — defensive margin against the
    child process's own shutdown/WAL-flush ordering, not because of
    TrackingDB's in-process batching (that's fully flushed by
    `TrackingDB.close()` in `quor/mcp/server.py::main()`'s `finally` block,
    which has already run by the time `stdio_client()`'s context manager
    returns and this function is called)."""
    deadline = time.monotonic() + _TELEMETRY_POLL_TIMEOUT_SECONDS
    count = _count_invocations(db_path)
    while count < minimum and time.monotonic() < deadline:
        time.sleep(0.1)
        count = _count_invocations(db_path)
    return count


class TestInitializeHandshake:
    def test_instructions_field_is_present_and_matches_server(
        self, isolated_quor_db: Path, tmp_path: Path
    ) -> None:
        """The `InitializeResult` returned over real stdio JSON-RPC must
        carry the QB-126 server-level instructions payload — asserted
        against the real `quor.mcp.server.INSTRUCTIONS` constant, not a
        duplicated literal in this test, so the two can never silently
        drift apart."""
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        from quor.mcp.server import INSTRUCTIONS

        async def _handshake() -> str | None:
            params = _server_params(tmp_path / "data")
            with anyio.fail_after(_HANDSHAKE_TIMEOUT_SECONDS):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        result = await session.initialize()
                        return result.instructions

        instructions = anyio.run(_handshake)

        assert instructions is not None
        assert instructions == INSTRUCTIONS
        assert "CRITICAL EXECUTION DIRECTIVE" in instructions
        assert "mcp__quor__compress_context" in instructions

    def test_server_name_is_reported(self, tmp_path: Path) -> None:
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async def _handshake() -> str:
            params = _server_params(tmp_path / "data")
            with anyio.fail_after(_HANDSHAKE_TIMEOUT_SECONDS):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        result = await session.initialize()
                        return result.server_info.name

        name = anyio.run(_handshake)

        assert name == "Quor Context Compressor"


class TestCompressContextTool:
    def test_returns_compressed_text(self, tmp_path: Path) -> None:
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        raw_text = "line one\nline two\nline three\n" * 30

        async def _call() -> tuple[bool, str]:
            params = _server_params(tmp_path / "data")
            with anyio.fail_after(_HANDSHAKE_TIMEOUT_SECONDS):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "compress_context", {"raw_text": raw_text}
                        )
                        text = "".join(getattr(block, "text", "") for block in result.content)
                        return result.is_error, text

        is_error, text = anyio.run(_call)

        assert is_error is False
        assert text.startswith("[Quor Compressed:")
        assert "tokens" in text

    def test_recorded_invocation_appears_in_quor_db(
        self, isolated_quor_db: Path, tmp_path: Path
    ) -> None:
        """The telemetry-recording assertion QB-129 asked for: an actual
        MCP tool call, over real stdio transport, against a real (isolated)
        quor.db — not `track_invocation_safe()` called directly in-process,
        which would prove far less."""
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        raw_text = "line one\nline two\nline three\n" * 30

        assert _count_invocations(isolated_quor_db) == 0

        async def _call() -> None:
            params = _server_params(tmp_path / "data")
            with anyio.fail_after(_HANDSHAKE_TIMEOUT_SECONDS):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.call_tool("compress_context", {"raw_text": raw_text})

        anyio.run(_call)

        count = _wait_for_invocation_count_at_least(isolated_quor_db, minimum=1)
        assert count >= 1


class TestGetRepoContextTool:
    def test_call_succeeds_with_deterministic_bailout_when_unmapped(
        self, tmp_path: Path
    ) -> None:
        """No `quor map` has run against the isolated cwd's repository
        intelligence cache, so this asserts the tool's real, documented
        behavior for that state (`server.py::get_repo_context`'s own
        docstring: "if it hasn't [been built], this tool says so instead
        of silently returning nothing") — still a real, successful
        stdio round-trip, not an error response."""
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async def _call() -> tuple[bool, str]:
            params = _server_params(tmp_path / "data")
            with anyio.fail_after(_HANDSHAKE_TIMEOUT_SECONDS):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            "get_repo_context", {"file_path": "quor/pipeline/tee.py"}
                        )
                        text = "".join(getattr(block, "text", "") for block in result.content)
                        return result.is_error, text

        is_error, text = anyio.run(_call)

        assert is_error is False
        assert "run `quor map`" in text
