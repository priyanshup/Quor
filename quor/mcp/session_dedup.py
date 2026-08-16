"""QB-089: exact-match, in-process session read deduplication for the MCP
server's `compress_context` tool.

**Why in-process memory is now correctly scoped (it wasn't before QB-104):**
QB-089's own "Open design questions" assumed "every hook invocation is a
brand-new process" (true under the old Claude Code `PreToolUse` Bash hook)
and expected an on-disk cache keyed by a session/transcript identifier as
the likely answer. QB-104 replaced that entire mechanism with
`quor/mcp/server.py`, an MCP server run over stdio — a stdio transport is a
1:1 parent↔child pipe, so each Claude Code session spawns and owns exactly
one Quor server subprocess for that session's whole lifetime (confirmed via
`docs/POC_TESTING.md`'s `mcpServers` registration, one `cwd`-scoped entry
per project). A plain in-memory cache in that process is therefore already
exactly session-scoped, with no cross-session leakage possible (a different
session is a different process, a different memory space) and no on-disk
state or `transcript_path` parsing needed.

**Why the cache is bounded, not whole-session-unbounded (product-owner
decision):** `compress_context(raw_text)` carries no path or call-count —
just text. An unbounded cache risks a real failure mode QB-089's own text
flags as unresolved ("cache invalidation and TTL need the same care QB-013's
tee cache already applies"): content shown many calls ago may have fallen
out of the AI's own live context (e.g. after a client-side compaction), and
unlike Quor's tee mechanism, `compress_context` has no path-based recovery
link a stale "unchanged" marker could point back to. Bounding to the most
recent `DEFAULT_CACHE_SIZE` distinct hashes keeps every suppression close
enough in the conversation to be virtually certain the AI still has it.
"""

from __future__ import annotations

from collections import OrderedDict

DEFAULT_CACHE_SIZE = 20


class SessionDedupCache:
    """A bounded LRU cache of content hashes seen this process's lifetime.

    `seen()` both checks and records in one call — a dedup cache is only
    ever useful read-then-write, and doing both atomically means a caller
    can't forget the second half or race between them.
    """

    def __init__(self, max_size: int = DEFAULT_CACHE_SIZE) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._seen: OrderedDict[str, None] = OrderedDict()

    def seen(self, content_hash: str) -> bool:
        """Return True if `content_hash` was already recorded (a cache hit
        — the caller should suppress full content) and refresh it as
        most-recently-used; False if this is the first time (a cache miss
        — the caller should show full content), recording it as
        most-recently-used and evicting the least-recently-used entry once
        `max_size` is exceeded."""
        hit = content_hash in self._seen
        if hit:
            self._seen.move_to_end(content_hash)
        else:
            self._seen[content_hash] = None
            if len(self._seen) > self._max_size:
                self._seen.popitem(last=False)
        return hit

    def __len__(self) -> int:
        return len(self._seen)
