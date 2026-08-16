"""Unit tests for quor/mcp/server.py's compress_context tool.

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
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import quor.mcp.server as mcp_server
from quor.mcp.server import compress_context
from quor.mcp.session_dedup import DEFAULT_CACHE_SIZE, SessionDedupCache


@pytest.fixture(autouse=True)
def _fresh_dedup_cache() -> Iterator[None]:
    original = mcp_server._dedup_cache
    mcp_server._dedup_cache = SessionDedupCache()
    try:
        yield
    finally:
        mcp_server._dedup_cache = original


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
