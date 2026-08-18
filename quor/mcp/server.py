"""Quor MCP server (QB-104) — the production replacement for Quor's former
hook-based integration (see backlog.md's QB-104 entry for the full removal/
rebuild plan). Exposes Quor's compression pipeline and repository
intelligence as MCP tools an agent calls explicitly, over stdio.

`compress_context` reuses the same `FilterRegistry`/`Pipeline` machinery
`quor/engine/dispatcher.py` already runs for Bash output (the promoted
POC's own logic, unchanged): `FilterRegistry.find()` matches a command/path-
shaped string, and raw text handed to this tool essentially never matches
one of the specific patterns (`^git\\s+log\\b`, `^cat\\s+...\\.py\\b`, etc. —
those match the *command*, not command *output*), so in practice this always
falls through to the built-in `generic` filter
(`quor/filters/builtin/z_generic.toml`, `match_command = '.'`), the same
catch-all `quor/engine/dispatcher.py` itself falls back to for any
unrecognized command.

`get_repo_context` ports the two genuinely repo-state-based Read-hook
features from the now-removed `quor/adapters/claude_read.py` (QB-079's
"Repository Context" block and QB-090's onboarding nudge) — both were
already protocol-agnostic (plain functions over `Path`/`str`, no coupling
to hook JSON shape), so porting them is additive, not a rewrite. QB-081's
third feature ("Relevant repository files") is also ported, but adapted:
the hook version parsed Claude Code's own transcript JSONL to find the
user's last prompt and derive query terms from it — an MCP tool call has
no such transcript to read, so `query` is instead a direct parameter the
calling agent passes.

Run directly: `python -m quor.mcp.server` (stdio transport). See
docs/POC_TESTING.md for how to register this with an MCP client.
"""

from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from quor.filters.registry import FilterRegistry
from quor.mcp.session_dedup import SessionDedupCache
from quor.pipeline.content_type import detect
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.pipeline.repo_profile.nudge import compute_hook_nudge
from quor.pipeline.repo_profile.query_extract import extract_query_terms
from quor.pipeline.repo_profile.search import merge_search
from quor.pipeline.repo_profile.search_render import render_relevant_files_block
from quor.pipeline.tee import content_hash
from quor.tracking.db import (
    MCP_DEDUP_FILTER_LABEL,
    MCP_REPO_CONTEXT_FILTER_LABEL,
    TrackingDB,
    count_tokens,
    get_tracking_db,
    track_invocation,
)

# The installed `mcp` SDK (v2.0.0) renamed the high-level server class from
# `FastMCP` (mcp.server.fastmcp) to `MCPServer` (mcp.server.mcpserver) with
# no back-compat alias — same decorator/`run(transport=...)` API otherwise.
mcp = MCPServer("Quor Context Compressor")

# QB-089: module-level, not per-call — this dict must live exactly as long
# as this process does (one MCP server subprocess per Claude Code session,
# stdio transport, see session_dedup.py's own docstring for why that makes
# this genuinely session-scoped rather than a global-state smell).
_dedup_cache = SessionDedupCache()

# QB-105: session-scoped, same lifetime reasoning as `_dedup_cache` above —
# but constructed lazily (on first real use, not at import time), unlike
# `_dedup_cache`. Unlike that in-memory dict, `TrackingDB.__init__` spins up
# a background thread and touches the real platformdirs `quor.db` on disk;
# doing that merely by importing this module (as every test in
# test_mcp_server.py already does) would be a real, surprising side effect.
# `_tracking_db_lock` only guards this one-time construction race — once
# built, `TrackingDB.record()` is itself safe for concurrent callers
# (`queue.SimpleQueue`).
_tracking_db: TrackingDB | None = None
_tracking_db_lock = threading.Lock()


def _get_tracking_db() -> TrackingDB:
    global _tracking_db
    if _tracking_db is None:
        with _tracking_db_lock:
            if _tracking_db is None:
                _tracking_db = get_tracking_db()
    return _tracking_db


def _track(
    *,
    command: str,
    original: str,
    filtered: str,
    filter_name: str | None,
    was_passthrough: bool,
    t0: float,
) -> None:
    """Fail-open wrapper around track_invocation() for this module's two MCP
    tools — mirrors every other producer's own try/except Exception: pass
    discipline (map.py/explore.py/...), needed here on top of
    track_invocation()'s own internal swallowing since constructing the lazy
    `_tracking_db` singleton happens outside that internal guard."""
    with contextlib.suppress(Exception):
        track_invocation(
            _get_tracking_db(),
            command=command,
            original=original,
            filtered=filtered,
            filter_name=filter_name,
            was_passthrough=was_passthrough,
            t0=t0,
        )


@mcp.tool()
def compress_context(raw_text: str) -> str:
    """Use this tool whenever reading large command outputs, log streams, git
    history, or long files (exceeding 30 lines). It compresses the input
    deterministically to conserve token context window space."""
    t0 = time.monotonic()
    original_tokens = count_tokens(raw_text)
    if original_tokens == 0:
        # Nothing was measured — untracked, same "empty file_path stays
        # untracked" convention track_invocation()'s own producers already
        # follow for a degenerate/no-op input.
        return "[Quor Compressed: 0% saved]\n" + raw_text

    # QB-089: exact-match session dedup — if this exact content was already
    # shown recently this session, resending the compressed version again
    # is pure waste. Keyed on raw_text (the source content), not the
    # compressed output, since "did the underlying content change" is the
    # only question that matters here.
    digest = content_hash(raw_text)
    if _dedup_cache.seen(digest):
        marker = f"[Quor: unchanged since last shown this session ({digest[:12]}) — see above]"
        _track(
            command="MCP compress_context",
            original=raw_text,
            filtered=marker,
            filter_name=MCP_DEDUP_FILTER_LABEL,
            was_passthrough=False,
            t0=t0,
        )
        return marker

    registry = FilterRegistry(project_root=Path.cwd())
    filter_config = registry.find(raw_text)
    if filter_config is None:
        compressed = raw_text
    else:
        content_type = detect(raw_text).value
        compressed = registry.apply(filter_config, raw_text, content_type=content_type)

    compressed_tokens = count_tokens(compressed)
    saved_pct = max(0, round((1 - compressed_tokens / original_tokens) * 100))
    result = f"[Quor Compressed: {saved_pct}% saved]\n{compressed}"
    _track(
        command="MCP compress_context",
        original=raw_text,
        filtered=result,
        filter_name=filter_config.name if filter_config is not None else None,
        was_passthrough=filter_config is None,
        t0=t0,
    )
    return result


_MAX_RELEVANT_FILES = 5
"""Mirrors the former Read-hook's own `MAX_RELEVANT_FILES` (QB-081) — kept
identical since the underlying `merge_search()` call and its result shape
are unchanged, only the query source (a direct parameter here, a parsed
transcript there) differs."""


@mcp.tool()
def get_repo_context(file_path: str = "", query: str = "") -> str:
    """Use this tool to get deterministic repository intelligence before
    editing or reasoning about a file: structural context for a specific
    file (its language, exported symbols, import/imported-by counts),
    and/or a list of files relevant to a search query. Requires repository
    intelligence to have been built already (run `quor map` first) — if it
    hasn't, this tool says so instead of silently returning nothing.

    file_path: repo-relative or absolute path to a file to show context for.
    query: free text (e.g. a symbol or feature name) to find relevant files for.
    """
    root = Path.cwd()
    t0 = time.monotonic()
    sections: list[str] = []

    entries = intel_store.load_file_intelligence(root)
    if entries is None:
        # Untracked — mirrors `quor map`'s own convention of only recording
        # an invocation on real output, not on a "go run `quor map`" bailout
        # that did no repository-intelligence lookup at all.
        nudge = _safe_nudge(root)
        return (
            "No repository intelligence found for this directory — run `quor map` "
            "first to build it." + (f"\n\n{nudge}" if nudge else "")
        )

    if file_path:
        block = _repo_context_block(root, entries, file_path)
        if block is not None:
            sections.append(block)
        else:
            sections.append(f"No repository intelligence entry for {file_path!r}.")

    if query:
        terms = extract_query_terms(query)
        if terms:
            rel_path = _relative_posix_path(file_path, root) if file_path else None
            exclude = frozenset({rel_path}) if rel_path is not None else frozenset()
            matches = merge_search(entries, terms, limit=_MAX_RELEVANT_FILES, exclude=exclude)
            if matches:
                sections.append(render_relevant_files_block(matches).rstrip("\n"))
            else:
                sections.append(f"No relevant files found for query {query!r}.")
        else:
            sections.append(f"No searchable terms extracted from query {query!r}.")

    if not file_path and not query:
        sections.append(
            f"Repository intelligence is available for {len(entries)} file(s) — "
            "pass file_path and/or query to use it."
        )

    nudge = _safe_nudge(root)
    if nudge:
        sections.append(nudge.rstrip("\n"))

    result = "\n\n".join(sections)
    if file_path:
        command = f"MCP get_repo_context: {file_path}"
    elif query:
        command = f"MCP get_repo_context: query={query!r}"
    else:
        command = "MCP get_repo_context: (no args)"
    _track(
        command=command,
        original=result,
        filtered=result,
        filter_name=MCP_REPO_CONTEXT_FILTER_LABEL,
        was_passthrough=False,
        t0=t0,
    )
    return result


def _relative_posix_path(file_path: str, root: Path) -> str | None:
    """Mirrors `claude_read.py`'s own `_relative_posix_path` — never raises,
    returns `None` for a path outside `root` (including a different-drive
    `ValueError` on Windows)."""
    try:
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        rel = candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return rel.as_posix()


def _repo_context_block(
    root: Path, entries: dict[str, FileIntelligenceEntry], file_path: str
) -> str | None:
    """Render QB-079's "Repository Context" block for `file_path`, or `None`
    if there's no fresh entry for it — mirrors `claude_read.py`'s
    `_maybe_prepend_repo_context`'s fail-open staleness check (size/mtime
    against the live file), minus the try/except (this tool's own caller
    handles unexpected errors)."""
    rel_path = _relative_posix_path(file_path, root)
    if rel_path is None:
        return None
    entry = entries.get(rel_path)
    if entry is None:
        return None
    try:
        st = (root / rel_path).stat()
    except OSError:
        return None
    if st.st_size != entry.size or st.st_mtime_ns != entry.mtime_ns:
        return None  # stale cache entry — omit rather than show possibly-wrong info

    defines = ", ".join(entry.top_symbols) if entry.top_symbols else "(none)"
    entry_point = "yes" if entry.entry_point else "no"
    return (
        f"Repository Context ({rel_path}):\n"
        f"  Kind: {entry.kind.capitalize()} | Language: {entry.language} | "
        f"Importance: {entry.importance} | Entry point: {entry_point}\n"
        f"  Defines: {defines}\n"
        f"  Imports: {entry.imports} file(s) | Imported by: {entry.imported_by} file(s)"
    )


def _safe_nudge(root: Path) -> str | None:
    try:
        return compute_hook_nudge(root)
    except Exception:  # noqa: BLE001 — fail-open: never let a nudge error surface
        return None


if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    finally:
        # QB-105: mirrors quor/__main__.py's own `tracking.close()` in a
        # `finally` around run_dispatch() — flush whatever's staged before
        # the process exits. Guarded on `is not None`: a session that never
        # actually tracked an invocation (every call degenerate/untracked)
        # should not force quor.db into existence on exit.
        if _tracking_db is not None:
            _tracking_db.close()
