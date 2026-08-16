"""Unit tests for QB-089's SessionDedupCache — the bounded LRU behind the
MCP server's compress_context exact-match dedup. See test_mcp_server.py for
compress_context's own wiring tests."""

from __future__ import annotations

import pytest

from quor.mcp.session_dedup import SessionDedupCache


class TestSessionDedupCache:
    def test_first_occurrence_is_a_miss(self) -> None:
        cache = SessionDedupCache()
        assert cache.seen("hash-a") is False

    def test_repeat_occurrence_is_a_hit(self) -> None:
        cache = SessionDedupCache()
        cache.seen("hash-a")
        assert cache.seen("hash-a") is True

    def test_different_hashes_are_independent(self) -> None:
        cache = SessionDedupCache()
        cache.seen("hash-a")
        assert cache.seen("hash-b") is False

    def test_len_reflects_distinct_hashes_recorded(self) -> None:
        cache = SessionDedupCache()
        cache.seen("hash-a")
        cache.seen("hash-b")
        cache.seen("hash-a")  # repeat, not a new entry
        assert len(cache) == 2

    def test_eviction_beyond_max_size(self) -> None:
        cache = SessionDedupCache(max_size=2)
        cache.seen("hash-a")
        cache.seen("hash-b")
        cache.seen("hash-c")  # evicts hash-a (least recently used)
        assert len(cache) == 2
        assert cache.seen("hash-a") is False  # was evicted, so this is a fresh miss
        assert cache.seen("hash-c") is True  # still present

    def test_hit_refreshes_recency_and_protects_from_eviction(self) -> None:
        """Re-seeing hash-a before hash-c arrives should keep hash-a (now
        most-recently-used) and evict hash-b instead."""
        cache = SessionDedupCache(max_size=2)
        cache.seen("hash-a")
        cache.seen("hash-b")
        cache.seen("hash-a")  # refresh: hash-a is now most-recently-used
        cache.seen("hash-c")  # evicts hash-b, not hash-a
        assert cache.seen("hash-a") is True
        assert cache.seen("hash-b") is False

    def test_max_size_one(self) -> None:
        cache = SessionDedupCache(max_size=1)
        cache.seen("hash-a")
        assert cache.seen("hash-b") is False
        assert cache.seen("hash-a") is False  # evicted already

    def test_invalid_max_size_raises(self) -> None:
        with pytest.raises(ValueError):
            SessionDedupCache(max_size=0)
