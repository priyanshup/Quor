"""Quor MCP server (QB-104) — the production replacement for Quor's former
hook-based integration (see backlog.md's QB-104 entry for the full removal/
rebuild plan). Exposes Quor's compression pipeline and repository
intelligence as MCP tools an agent calls explicitly, over stdio.

`compress_context` runs through `quor/engine/dispatcher.py`'s
`apply_filter_pipeline()` (QB-114, MCP Dispatcher Parity) — the same
filter-lookup → PRE_FILTER → ContentMask → POST_FILTER → tee → secret-scan
sequence `run_dispatch()` runs for CLI/Bash output, not a parallel
reimplementation. `FilterRegistry.find()` matches a command/path-shaped
string, and raw text handed to this tool essentially never matches one of
the specific patterns (`^git\\s+log\\b`, `^cat\\s+...\\.py\\b`, etc. — those
match the *command*, not command *output*), so in practice this always
falls through to the built-in `generic` filter
(`quor/filters/builtin/z_generic.toml`, `match_command = '.'`), the same
catch-all `quor/engine/dispatcher.py` itself falls back to for any
unrecognized command. Before QB-114, this tool called `FilterRegistry.find()`/
`.apply()` directly and skipped `write_tee()` (QB-013's recovery-link
generation) and `scan_for_secrets()` (QB-029/PA-F07) entirely — both fire
unconditionally on the CLI dispatch path, so an MCP tool call was a real,
silent safety-net gap relative to a CLI-run command doing the same
compression.

`get_repo_context` doesn't route through the same pipeline — its output is
synthesized repository-intelligence metadata, not a captured command's
stdout being compressed, so there's no raw discarded original for tee to
make recoverable, and running ContentMask filter stages against its
already-curated structure would risk corrupting it rather than compressing
it. It still gets the same secret-scan safety net (`dispatcher.
_scan_secrets_safe()`) applied to its result before returning, since that
check is generic to any text crossing the transport boundary, not specific
to command output.

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

import logging
import threading
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from quor.engine.dispatcher import _scan_secrets_safe, apply_filter_pipeline
from quor.mcp.logging_config import LOGGER_NAME, configure_logging
from quor.mcp.session_dedup import SessionDedupCache
from quor.pipeline.git_diff_enrich import count_diff_files
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
    track_invocation_safe,
)

# The installed `mcp` SDK (v2.0.0) renamed the high-level server class from
# `FastMCP` (mcp.server.fastmcp) to `MCPServer` (mcp.server.mcpserver) with
# no back-compat alias — same decorator/`run(transport=...)` API otherwise.
mcp = MCPServer("Quor Context Compressor")

_logger = logging.getLogger(LOGGER_NAME)

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
        track_invocation_safe(
            _get_tracking_db,
            command="MCP compress_context",
            original=raw_text,
            filtered=marker,
            filter_name=MCP_DEDUP_FILTER_LABEL,
            t0=t0,
        )
        return marker

    # QB-114: routes through the same filter-lookup → PRE_FILTER →
    # ContentMask → POST_FILTER → tee → secret-scan pipeline `run_dispatch()`
    # runs for CLI/Bash output — `write_tee()` now fires here too (a
    # recovery footer may be appended to `compressed` below), and
    # `scan_for_secrets()` now runs on the result before it's returned.
    compressed, filter_config = apply_filter_pipeline(raw_text, raw_text)

    compressed_tokens = count_tokens(compressed)
    saved_pct = max(0, round((1 - compressed_tokens / original_tokens) * 100))
    result = f"[Quor Compressed: {saved_pct}% saved]\n{compressed}"
    # QB-109: files_changed (QB-093 telemetry prep) was only ever wired into
    # dispatcher.py's git-diff call site, which is dead for real usage since
    # QB-104 made this tool the sole integration surface — 0 of the DB's
    # real git-diff rows had it populated. Now that match_content_types lets
    # git-diff actually be selected here, record it at the site that's
    # actually reachable.
    files_changed = (
        count_diff_files(raw_text)
        if filter_config is not None and filter_config.name == "git-diff"
        else None
    )
    track_invocation_safe(
        _get_tracking_db,
        command="MCP compress_context",
        original=raw_text,
        filtered=result,
        filter_name=filter_config.name if filter_config is not None else None,
        was_passthrough=filter_config is None,
        t0=t0,
        files_changed=files_changed,
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
        bailout = (
            "No repository intelligence found for this directory — run `quor map` "
            "first to build it." + (f"\n\n{nudge}" if nudge else "")
        )
        _scan_secrets_safe(bailout)
        return bailout

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
    track_invocation_safe(
        _get_tracking_db,
        command=command,
        original=result,
        filter_name=MCP_REPO_CONTEXT_FILTER_LABEL,
        t0=t0,
    )
    # QB-114: no ContentMask/tee here (see module docstring) — this result
    # isn't a captured command's stdout being compressed — but the same
    # secret-scan safety net still applies before it crosses the transport.
    _scan_secrets_safe(result)
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


def main() -> None:
    """Block on the stdio MCP loop. Extracted from the `__main__` guard so
    `quor/mcp/launcher.py` — the self-healing pre-flight entrypoint that
    `quor init --mcp` now scaffolds instead of this module directly — can
    hand off to it after confirming dependencies are actually importable."""
    configure_logging()
    _logger.info("Quor MCP server starting (stdio transport)")
    try:
        mcp.run(transport="stdio")
    except Exception:
        _logger.exception("Quor MCP server crashed")
        raise
    finally:
        # QB-105: mirrors quor/__main__.py's own `tracking.close()` in a
        # `finally` around run_dispatch() — flush whatever's staged before
        # the process exits. Guarded on `is not None`: a session that never
        # actually tracked an invocation (every call degenerate/untracked)
        # should not force quor.db into existence on exit.
        _logger.info("Quor MCP server shutting down")
        if _tracking_db is not None:
            _tracking_db.close()


if __name__ == "__main__":
    main()
