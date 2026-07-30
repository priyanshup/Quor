"""Claude Code PostToolUse/Read hook adapter.

Reads a Claude Code PostToolUse JSON payload from sys.stdin, routes the
Read tool's file path through the existing `FilterRegistry` (QB-007C) —
exactly the same registry/`Pipeline` machinery `quor/adapters/dispatcher.py`
already uses for Bash output, just matched against a file path instead of a
command string (see `quor/filters/builtin/markdown.toml`'s header comment
for why that reuse is safe). If a filter matches and actually changes the
content, the compressed result is returned via `updatedToolOutput`; in
every other case (no filter match, compression is a no-op, or anything
raises) `updatedToolOutput` is omitted and Claude Code keeps the original
Read result — the same "omit the key rather than emit an empty/wrong value"
discipline ADR-030 established for `updatedInput`.

`.md`/`.markdown`/`.txt`/`.rst` files compress directly (QB-007B's
`markdown`/`document-text` filters), matched by file path exactly as before.
This is enforced by an explicit filter-name allowlist
(`_READ_SUPPORTED_FILTER_NAMES`), not by the absence of a `FilterRegistry`
match: the Bash-oriented `generic` filter matches literally every non-empty
string (including any file path), so without the allowlist an unsupported
file would be silently routed through a shell-output filter never designed
for document content. See that constant's own comment for the full
rationale.

`.docx`/`.pdf` files (QB-007E4) take a second path: the binary document is
first converted to Markdown-shaped text by `quor/pipeline/extract`
(QB-007E1/E2/E3, reused here completely unchanged — see
`_compress_extracted_document`'s own docstring), and *that* text is then run
through the exact same `markdown` `FilterConfig` the `.md` path already
uses — looked up by name via `FilterRegistry.all_filters()`, not by a new
`docx.toml`/`pdf.toml` file path pattern, so there is still only one
Markdown-compression filter in the entire system.

`.py`/`.js`/`.jsx`/`.mjs`/`.cjs`/`.ts`/`.tsx` files (QB-005F) take a third
path, for the same structural reason as `.docx`/`.pdf`: the `cat-python`/
`cat-javascript`/`cat-typescript`/`cat-tsx` filters (QB-005B-D) already exist
and already wrap the AST-summarization pipeline, but their `match_command`
patterns are all shaped like `^cat\\s+...\\.py\\b` — a literal `cat `-prefixed
command string a bare Read `file_path` (e.g. `"src/app.py"`) can never
match. So, exactly like the extracted-document path, the matching filter is
looked up **by name** (`_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION`, keyed by
extension) instead of by `FilterRegistry.find(file_path)`. Unlike the
document path there is no extraction step — the Read `tool_response` is
already the plain source text — so both the content to filter and the
content to compare against are the same `tool_response` value. Both this
path and the document path share their post-lookup "apply, track, omit if
unchanged" tail via `_compress_via_named_filter()`, added in QB-005F when
this exact duplication was identified between the two by-name paths.
Everything else (unsupported extensions, no extension, an extraction
failure, a missing AST dependency, a parse/filter failure, ...) still passes
through unchanged, exactly as before QB-005F.

`.json`/`.jsonc`/`.yaml`/`.yml`/`.toml` files (QB-040) take a fourth path,
structurally identical to the source-code path immediately above (by-name
lookup via `_STRUCTURED_DATA_FILTER_NAMES_BY_EXTENSION`, straight to
`_compress_via_named_filter` — no extraction step, `content`/`original`
both `tool_response`): `cat-json.toml`/`cat-yaml.toml`/`cat-toml.toml`'s
`match_command` patterns are `cat`/`type`-command-shaped for the same
reason `cat-python.toml`'s are, so they need the same by-extension
redirection a bare Read `file_path` could never trigger via
`FilterRegistry.find()`. `.env`/`.ini` files (also QB-040) do NOT take this
path — `cat-dotenv.toml`/`cat-ini.toml` match a bare file path directly (see
those files' own `match_command`), so they only need a
`_READ_SUPPORTED_FILTER_NAMES` allowlist entry, exactly like `.md`/`.txt`.

Called by __main__._run_hook() (adapter name "claude-read"), which
guarantees:
- sys.stdin is a TextIOWrapper over the original stdin bytes
- Any exception raised here is caught by __main__ and returns original bytes
  (a second, outermost fail-open layer on top of the one in
  `_compress_read_output` below)
- A TrackingDB is constructed and passed in as `tracking` (QB-007D), exactly
  as `__main__._run_dispatch()` already does for the Bash path — see
  `_compress_read_output`'s own docstring for what gets recorded and when.
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path
from typing import Any

import orjson

from quor.adapters.base import PostToolUseHookInput
from quor.adapters.dispatcher import CONCISE_INSTRUCTION
from quor.config.model import FilterConfig
from quor.filters.registry import FilterRegistry
from quor.pipeline.extract.registry import extract
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.pipeline.repo_profile.query_extract import extract_query_terms
from quor.pipeline.repo_profile.search import merge_search
from quor.pipeline.repo_profile.search_render import render_relevant_files_block
from quor.tracking.db import TrackingDB, track_invocation

# ---------------------------------------------------------------------------
# Hook script template — written by `quor init --claude` (QB-007A)
# Placeholders: {python} is replaced with sys.executable at install time.
# ---------------------------------------------------------------------------

HOOK_READ_COMMAND = "{python} -m quor hook claude-read"

HOOK_READ_PS1_TEMPLATE = """\
# Quor PostToolUse/Read hook script — generated by `quor init --claude`
# quor-hook-schema: {schema_version}
# Do not edit this file. To update, run `quor init --claude` again.
$ErrorActionPreference = 'Stop'
$json = [Console]::In.ReadToEnd()
$json | & '{python}' -m quor hook claude-read
"""

# QB-082: POSIX (macOS/Linux) equivalent of HOOK_READ_PS1_TEMPLATE — see
# quor/adapters/claude.py's HOOK_SH_TEMPLATE for why `exec` replaces the
# read-all-then-pipe pattern PowerShell needs.
HOOK_READ_SH_TEMPLATE = """\
#!/bin/sh
# Quor PostToolUse/Read hook script — generated by `quor init --claude`
# quor-hook-schema: {schema_version}
# Do not edit this file. To update, run `quor init --claude` again.
exec "{python}" -m quor hook claude-read
"""

_UTF8_BOM = "﻿"

# Explicit allowlist of filter names this adapter will actually apply.
#
# FilterRegistry is shared with the Bash path, whose built-in `generic`
# filter (quor/filters/builtin/z_generic.toml) matches every non-empty
# string via `match_command = '.'` — including a bare Read file path like
# "report.docx" or "script.py", since FilterRegistry has no concept of
# "this string is a file path, not a command". Without this allowlist,
# every unsupported file type would silently be routed to `generic`'s
# ANSI-strip/dedupe/max_tokens(1000, tail) pipeline — a shell-output filter
# never designed for, or tested against, arbitrary document content, and
# exactly the kind of accidental scope expansion QB-007C's requirements
# explicitly rule out ("unsupported file types pass through unchanged").
# Extend this set (not FilterRegistry itself) if a future format needs its
# own dedicated filter — QB-007E4's DOCX/PDF integration deliberately does
# NOT add to this set; extracted DOCX/PDF text is routed to the existing
# "markdown" entry by name (see _MARKDOWN_FILTER_NAME), not matched as a
# file path, so it never needs its own filter-name allowlist entry.
#
# "dotenv"/"ini" (QB-040) join this set for the same reason "markdown"/
# "document-text" are here: quor/filters/builtin/cat-dotenv.toml/cat-ini.toml
# match a bare Read file_path directly (an alternation that also matches a
# `cat`/`type` command — see those files' own match_command), so
# FilterRegistry.find(file_path) already resolves them correctly; they just
# need to clear this allowlist instead of falling through to "generic".
# "json"/"yaml"/"toml" are NOT added here — their filters' match_command
# patterns are shaped like cat-python.toml's (`^cat\s+...` only, never a bare
# path), so they are looked up by extension instead, exactly like
# _SOURCE_CODE_FILTER_NAMES_BY_EXTENSION below (see
# _STRUCTURED_DATA_FILTER_NAMES_BY_EXTENSION).
_READ_SUPPORTED_FILTER_NAMES: frozenset[str] = frozenset(
    {"markdown", "document-text", "dotenv", "ini"}
)

# Extensions routed through quor/pipeline/extract (QB-007E1/E2/E3) before
# filtering — the mirror of _READ_SUPPORTED_FILTER_NAMES above, but for the
# extraction path rather than the direct file-path-match path. Kept here
# (not read from quor.pipeline.extract.registry's own routing table) for
# the same reason _READ_SUPPORTED_FILTER_NAMES is adapter-local rather than
# derived from FilterRegistry: this adapter decides what it will *attempt*,
# independent of what the lower layer happens to support today.
_EXTRACTION_EXTENSIONS: frozenset[str] = frozenset({".docx", ".pdf"})

# The one filter extracted DOCX/PDF Markdown-shaped text is compressed
# with — the existing "markdown" FilterConfig (quor/filters/builtin/
# markdown.toml), looked up by name via FilterRegistry.all_filters(), not
# matched against a file path the way the direct .md/.txt path is. No
# docx.toml/pdf.toml exists or is created — see module docstring.
_MARKDOWN_FILTER_NAME = "markdown"

# Extension -> builtin filter name, for source-code Read files (QB-005F).
# Mirrors _MARKDOWN_FILTER_NAME's by-name lookup, not _READ_SUPPORTED_FILTER_NAMES'
# path-match lookup — see module docstring for why: cat-python.toml/
# cat-javascript.toml/cat-typescript.toml's `match_command` patterns all
# require a literal "cat "-prefixed command string, which a bare Read
# file_path can never match via FilterRegistry.find(). Each of these filters
# already exists (QB-005B/C/D) and already wraps the AST-summarization
# pipeline (code_ast_summarize/python_ast_summarize stages) — this mapping
# only makes them *reachable* from the Read hook; no new filter is added
# here. ".jsx"/".mjs"/".cjs" all share cat-javascript, exactly as that
# filter's own match_command already treats them as one language for the
# Bash path.
_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION: dict[str, str] = {
    ".py": "cat-python",
    ".js": "cat-javascript",
    ".jsx": "cat-javascript",
    ".mjs": "cat-javascript",
    ".cjs": "cat-javascript",
    ".ts": "cat-typescript",
    ".tsx": "cat-tsx",
}

# Extension -> builtin filter name, for structured-data Read files (QB-040).
# Same by-name-lookup reasoning as _SOURCE_CODE_FILTER_NAMES_BY_EXTENSION
# immediately above (its own docstring explains why: cat-json.toml/
# cat-yaml.toml/cat-toml.toml's `match_command` patterns are all
# `^cat\s+...`-shaped, which a bare Read file_path can never match via
# FilterRegistry.find()) — kept as its own dict rather than folded into that
# one so each dict's name stays accurate to what it actually contains
# (source code vs. structured data); both are consulted identically in
# `_compress_read_output` below via `_compress_via_named_filter`, so there is
# no duplicated dispatch logic, only the mapping itself is separate.
_STRUCTURED_DATA_FILTER_NAMES_BY_EXTENSION: dict[str, str] = {
    ".json": "cat-json",
    ".jsonc": "cat-json",
    ".yaml": "cat-yaml",
    ".yml": "cat-yaml",
    ".toml": "cat-toml",
}

# Basename -> builtin filter name, for well-known lockfiles whose format
# isn't reflected in their extension (`poetry.lock`/`Cargo.lock` are TOML,
# `Pipfile.lock`/`composer.lock` are JSON — all four use `.lock`, which
# `_STRUCTURED_DATA_FILTER_NAMES_BY_EXTENSION` above cannot route). Checked
# by exact basename, not suffix — mirrors `cat-toml.toml`/`cat-json.toml`'s
# own `match_command` alternation for the Bash `cat` path (see those files'
# header comments for why these four specifically).
_STRUCTURED_DATA_FILTER_NAMES_BY_BASENAME: dict[str, str] = {
    "poetry.lock": "cat-toml",
    "Cargo.lock": "cat-toml",
    "Pipfile.lock": "cat-json",
    "composer.lock": "cat-json",
}


def run_hook(*, tracking: TrackingDB | None = None) -> None:
    """Process one PostToolUse/Read hook call.

    Reads JSON from sys.stdin (already set up by __main__._run_hook).
    Writes a `hookSpecificOutput` response to sys.stdout.
    Raises on parse/validation errors — caller (__main__) handles fail-open
    for those; content-routing/compression failures are handled locally by
    `_compress_read_output`, which never raises.

    `tracking` is optional (defaults to None, a no-op) so existing callers
    that don't care about tracking — direct unit tests, in particular — are
    unaffected; `__main__._run_hook()` passes a real `TrackingDB` in
    production, exactly as `_run_dispatch()` already does for Bash.

    When `_compress_read_output` returns non-`None` — i.e. Quor is genuinely
    handing back compressed content via `updatedToolOutput` — the same
    `CONCISE_INSTRUCTION` the Bash dispatcher prepends
    (`quor/adapters/dispatcher.py`) is prepended here too, so the assistant
    gets a consistent nudge regardless of which path produced the compressed
    text. `None` (passthrough/fail-open — original Read result kept) is left
    untouched, exactly as before.
    """
    raw = sys.stdin.read()
    sys.stdout.buffer.write(_handle_text(raw, tracking))
    sys.stdout.buffer.flush()


def handle_bytes(raw_stdin: bytes, *, tracking: TrackingDB | None = None) -> bytes:
    """Bytes-in/bytes-out equivalent of `run_hook()` — the entry point
    `ClaudeAdapter.handle_event()` (QB-035B) calls. See
    `quor.adapters.claude.handle_bytes` for the identical rationale (ADR-036)
    and the same UTF-8-strict decoding equivalence to `sys.stdin.read()`."""
    return _handle_text(raw_stdin.decode("utf-8"), tracking)


def _handle_text(raw: str, tracking: TrackingDB | None) -> bytes:
    """Shared core of `run_hook()`/`handle_bytes()`: parse a PostToolUse/Read
    payload, compress if applicable, and return the serialized
    `hookSpecificOutput` response bytes. Raises on parse/validation errors —
    both callers rely on their own outer fail-open guard."""
    # Strip UTF-8 BOM (single or doubled — Cursor sends doubled BOM on Windows),
    # same as quor/adapters/claude.py.
    raw = raw.lstrip(_UTF8_BOM)

    data: dict[str, Any] = orjson.loads(raw)

    # Validate the fields we care about (raises ValidationError on bad shape).
    hook_input = PostToolUseHookInput.model_validate(data)

    hook_specific: dict[str, Any] = {"hookEventName": "PostToolUse"}

    compressed = _compress_read_output(hook_input, tracking)
    original_response = hook_input.tool_response if isinstance(hook_input.tool_response, str) else None
    with_relevant_files = _maybe_prepend_relevant_files(
        hook_input, compressed if compressed is not None else original_response
    )
    if with_relevant_files is not None:
        compressed = with_relevant_files
    if compressed is not None:
        if not compressed.startswith(CONCISE_INSTRUCTION):
            compressed = CONCISE_INSTRUCTION + compressed
        hook_specific["updatedToolOutput"] = compressed

    return orjson.dumps({"hookSpecificOutput": hook_specific})


def _compress_read_output(
    hook_input: PostToolUseHookInput, tracking: TrackingDB | None
) -> str | None:
    """Return compressed content if (and only if) a filter matched *and*
    actually changed the content; return None in every other case.

    None means "the caller must omit updatedToolOutput" — the single
    fail-open contract this function exists to guarantee, covering:
      - `tool_response` is missing or not a plain string (e.g. a future/
        unknown shape for a binary Read — QB-007B/C are text-only by
        design; see backlog.md's QB-007 entry).
      - `tool_input.file_path` is empty.
      - No filter matches the file path (unsupported extension, or a path
        containing whitespace — see markdown.toml's routing-anchor
        rationale), or a filter matched but isn't one of the document
        filters this adapter is scoped to (see `_READ_SUPPORTED_FILTER_NAMES`)
        — most notably `generic`, which would otherwise catch every
        unsupported file type via its own `match_command = '.'`.
      - The matched filter's rendered output is byte-identical to the
        original (nothing to report — mirrors ADR-030's identical
        "omit if unchanged" rule for the Bash adapter's `updatedInput`).
      - Anything raises — filter registry construction, routing, or
        pipeline execution. Caught here (not just by __main__'s outer
        guard) so a single bad filter can't take down routing for every
        other Read call in the same process; mirrors
        `quor/adapters/dispatcher.py`'s own filter-layer try/except.

    Tracking (QB-007D): every branch that represents a genuine Read
    invocation — i.e. `file_path` is non-empty — calls `track_invocation()`
    exactly once before returning, mirroring how `dispatcher.py` tracks
    every Bash invocation it dispatches. The `command` column gets
    `"Read: {file_path}"` so Read rows are self-describing next to Bash
    rows in the same table; no schema change, no new storage. A `file_path`
    of "" is the one case treated as "nothing happened" and left untracked
    — the same way `dispatcher.run_dispatch([])` returns before dispatching
    or tracking anything for empty `args`.

    The passthrough/filter-name split mirrors `dispatcher.py`'s own
    `_lookup_filter`/`_apply_content_filter` split exactly:
      - no match, or a match outside `_READ_SUPPORTED_FILTER_NAMES`
        (including a `FilterRegistry` construction/lookup error) →
        `filter_name=None, was_passthrough=True` — nothing was applied.
      - a supported filter matched, whether or not applying it changed the
        content or raised (fail-open falls back to the original response) →
        `filter_name=<name>, was_passthrough=False` — a filter was
        genuinely attempted, same as dispatcher tracking `filter_config.name`
        even when `_apply_content_filter` itself caught an error.

    QB-007E4: a `.docx`/`.pdf` file path (`_EXTRACTION_EXTENSIONS`) is
    diverted to `_compress_extracted_document` before any of the above —
    see that function's own docstring for its tracking/fail-open contract,
    which mirrors this one exactly (extraction failure ≈ "no filter
    matched"; a successful extraction ≈ "the markdown filter matched").

    QB-005F: a source-code file path whose extension is a key in
    `_SOURCE_CODE_FILTER_NAMES_BY_EXTENSION` is diverted straight to
    `_compress_via_named_filter` (no extraction step, no separate branch
    function — the shared helper is called directly, since there's nothing
    else this branch needs to do first). Tracking/fail-open contract is the
    same shared helper both this path and `_compress_extracted_document`
    use, so it mirrors both exactly.

    QB-079: once that source-code branch produces genuinely changed
    content, `_maybe_prepend_repo_context()` is given one chance to prepend
    a compact "Repository Context" block sourced from the already-cached
    `file_intelligence.json` (`intel_store.load_file_intelligence()` — a
    single O(1) dict lookup, never `ensure_repo_intelligence()` or
    `walk_repository()`). It fails open at every step (missing cache, no
    entry for this path, a stale size/mtime match) back to `compressed`
    completely unchanged — see that function's own docstring.

    Every other extension keeps using the exact code below, unmodified.

    QB-081: independently of everything above (and regardless of which
    branch handles this Read, including the plain passthrough case at the
    very bottom), `_handle_text()` — not this function — gives
    `_maybe_prepend_relevant_files()` one chance to prepend a "Relevant
    repository files" section afterward, built from `quor.pipeline.
    repo_profile.search.merge_search()` fed with deterministic query terms
    extracted from the user's own most recent prompt text. See that
    function's own docstring for its fail-open chain.
    """
    tool_response = hook_input.tool_response
    file_path = hook_input.tool_input.file_path
    if not file_path:
        return None

    t0 = time.monotonic()
    command = f"Read: {file_path}"

    if not isinstance(tool_response, str):
        track_invocation(
            tracking,
            command=command,
            original="",
            filtered="",
            filter_name=None,
            was_passthrough=True,
            t0=t0,
        )
        return None

    suffix = Path(file_path).suffix.lower()

    if suffix in _EXTRACTION_EXTENSIONS:
        return _compress_extracted_document(
            file_path=file_path,
            tool_response=tool_response,
            tracking=tracking,
            t0=t0,
            command=command,
        )

    source_code_filter = _SOURCE_CODE_FILTER_NAMES_BY_EXTENSION.get(suffix)
    if source_code_filter is not None:
        compressed = _compress_via_named_filter(
            content=tool_response,
            original=tool_response,
            filter_name=source_code_filter,
            tracking=tracking,
            t0=t0,
            command=command,
        )
        if compressed is not None:
            compressed = _maybe_prepend_repo_context(file_path, compressed)
        return compressed

    named_filter = _STRUCTURED_DATA_FILTER_NAMES_BY_EXTENSION.get(suffix) or _STRUCTURED_DATA_FILTER_NAMES_BY_BASENAME.get(
        Path(file_path).name
    )
    if named_filter is not None:
        return _compress_via_named_filter(
            content=tool_response,
            original=tool_response,
            filter_name=named_filter,
            tracking=tracking,
            t0=t0,
            command=command,
        )

    try:
        registry = FilterRegistry(project_root=Path.cwd())
        filter_config = registry.find(file_path)
    except Exception as exc:  # noqa: BLE001 — fail-open: never let a filter error surface
        warnings.warn(f"[quor] Read filter error: {exc}", stacklevel=2)
        filter_config = None

    if filter_config is None or filter_config.name not in _READ_SUPPORTED_FILTER_NAMES:
        track_invocation(
            tracking,
            command=command,
            original=tool_response,
            filtered=tool_response,
            filter_name=None,
            was_passthrough=True,
            t0=t0,
        )
        return None

    try:
        rendered = registry.apply(filter_config, tool_response)
    except Exception as exc:  # noqa: BLE001 — fail-open: never let a filter error surface
        warnings.warn(f"[quor] Read filter error: {exc}", stacklevel=2)
        rendered = tool_response

    track_invocation(
        tracking,
        command=command,
        original=tool_response,
        filtered=rendered,
        filter_name=filter_config.name,
        was_passthrough=False,
        t0=t0,
    )

    if rendered == tool_response:
        return None
    return rendered


def _compress_extracted_document(
    *,
    file_path: str,
    tool_response: str,
    tracking: TrackingDB | None,
    t0: float,
    command: str,
) -> str | None:
    """DOCX/PDF branch of `_compress_read_output` (QB-007E4).

    Desired pipeline (per QB-007E4's own spec):
        Read request → claude_read.py → extract(file_path) →
        FilterRegistry ("markdown", by name) → Pipeline → updatedToolOutput

    `quor.pipeline.extract.registry.extract()` is reused completely
    unchanged — it already guarantees fail-open (returns `None`, never
    raises) for a missing dependency, corrupt/encrypted file, or any other
    extraction failure; that `None` is treated exactly like "no filter
    matched" in the non-extraction path above. The `try/except` around the
    call is defense-in-depth (mirroring every other call in this file),
    not a sign that `extract()`'s own contract is doubted.

    Once extraction succeeds, the resulting Markdown-shaped text is run
    through the *existing* `"markdown"` `FilterConfig`
    (`quor/filters/builtin/markdown.toml`) via `_compress_via_named_filter`
    — looked up by name via `FilterRegistry.all_filters()`, not matched
    against `file_path` (which is still `"report.docx"`, not something
    `markdown.toml`'s `^\\S+\\.(md|markdown)$` pattern would ever match). No
    new filter file, no new routing system, no new stage —
    `FilterRegistry.apply()` is called exactly as it already is everywhere
    else.

    Tracking mirrors `_compress_read_output`'s own contract precisely:
      - extraction returns `None` → `filter_name=None, was_passthrough=True`
        — nothing was applied, same as "no filter matched" above.
      - extraction succeeds → delegated to `_compress_via_named_filter`,
        which tracks `filter_name="markdown", was_passthrough=False`
        whether or not the markdown filter changes the result, or even if
        applying it fails and falls back to the unfiltered extracted text —
        a filter was genuinely attempted, same as the non-extraction path
        tracks `filter_config.name` even when `_apply_content_filter`-
        equivalent handling caught an error.
      - `original_tokens` is always derived from the *original*
        `tool_response` (the raw Read result before extraction, passed as
        `_compress_via_named_filter`'s `original`), and `final_tokens` from
        whatever is actually returned as `updatedToolOutput` (or, on a
        filter failure, the unfiltered extracted text that was actually
        returned) — `track_invocation()` computes both via `count_tokens()`
        exactly as it already does for every other producer.

    Returns `None` (omit `updatedToolOutput`) if extraction failed, or if
    the final result happens to equal the original `tool_response` byte
    for byte (mirrors ADR-030's "omit if unchanged" rule) — never raises.
    """
    try:
        extracted = extract(Path(file_path))
    except Exception as exc:  # noqa: BLE001 — fail-open: extraction must never surface here
        warnings.warn(f"[quor] Read extraction error: {exc}", stacklevel=2)
        extracted = None

    if extracted is None:
        track_invocation(
            tracking,
            command=command,
            original=tool_response,
            filtered=tool_response,
            filter_name=None,
            was_passthrough=True,
            t0=t0,
        )
        return None

    return _compress_via_named_filter(
        content=extracted,
        original=tool_response,
        filter_name=_MARKDOWN_FILTER_NAME,
        tracking=tracking,
        t0=t0,
        command=command,
    )


def _compress_via_named_filter(
    *,
    content: str,
    original: str,
    filter_name: str,
    tracking: TrackingDB | None,
    t0: float,
    command: str,
) -> str | None:
    """Look up `filter_name` by name (not by `FilterRegistry.find()`'s
    command/path-pattern match), apply it to `content`, track the
    invocation, and return the rendered result — or `None` if it's
    unchanged from `original`. Never raises.

    Extracted in QB-005F from `_compress_extracted_document`'s own
    post-extraction tail, once the source-code Read path (below) needed the
    identical "by-name lookup -> apply -> track -> omit if unchanged"
    sequence. The only real difference between the two callers is *how*
    `content` was obtained (extracted from a binary document vs. already
    plain source text) — everything after that point was byte-for-byte
    identical duplicated code, so only that shared tail was extracted, not
    a new generic routing/dispatch layer.

    `content` and `original` are deliberately separate parameters because
    they differ for `_compress_extracted_document` (`content` is the
    extracted Markdown text, `original` is the raw pre-extraction
    `tool_response`, so tracking's token-savings numbers stay anchored to
    what Claude actually received from Read) but are the same value for the
    source-code path (there is no extraction step, so both are
    `tool_response`).

    Every failure mode (registry construction, filter lookup, `apply()`)
    falls open to `content` unchanged, exactly like every other filter call
    in this module — a warning is emitted, nothing raises past this
    function.
    """
    try:
        registry = FilterRegistry(project_root=Path.cwd())
        filter_config = _find_filter_by_name(registry, filter_name)
        rendered = registry.apply(filter_config, content) if filter_config is not None else content
    except Exception as exc:  # noqa: BLE001 — fail-open: never let a filter error surface
        warnings.warn(f"[quor] Read filter error: {exc}", stacklevel=2)
        rendered = content

    track_invocation(
        tracking,
        command=command,
        original=original,
        filtered=rendered,
        filter_name=filter_name,
        was_passthrough=False,
        t0=t0,
    )

    if rendered == original:
        return None
    return rendered


def _find_filter_by_name(registry: FilterRegistry, name: str) -> FilterConfig | None:
    """Look up a `FilterConfig` by its `name` field (project > user > builtin
    priority, same as `FilterRegistry.find()`'s own tier order) rather than
    by matching a command/file-path string — `FilterRegistry` has no
    built-in "find by name" method, so this composes it from the existing
    public `all_filters()` rather than adding one. `FilterRegistry` itself
    is not modified."""
    for _tier, filter_config in registry.all_filters():
        if filter_config.name == name:
            return filter_config
    return None


# ---------------------------------------------------------------------------
# QB-079: Repository Context — an O(1) lookup against file_intelligence.json
# ---------------------------------------------------------------------------


def _relative_posix_path(file_path: str, root: Path) -> str | None:
    """`tool_input.file_path` is very likely absolute; `file_intelligence.json`
    is keyed by repo-relative POSIX paths (`walk_repository()`'s own
    convention — see `intel.py`). Returns `None` (never raises) for a path
    outside `root`, including a different-drive `ValueError`
    `Path.relative_to()` raises on Windows."""
    try:
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        rel = candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return rel.as_posix()


def _maybe_prepend_repo_context(file_path: str, rendered: str) -> str:
    """Prepend a compact "Repository Context" block to `rendered` if (and
    only if) `file_intelligence.json` has a fresh entry for this path.
    Fails open at every step — any missing/corrupt/out-of-scope/stale
    condition returns `rendered` completely unchanged, never raises, and
    never shows possibly-wrong information (mirrors ADR-030's "omit rather
    than guess" discipline, applied to this block instead of
    `updatedToolOutput` itself).

    Never calls `ensure_repo_intelligence()` or `walk_repository()` — the
    single `intel_store.load_file_intelligence()` call below, plus one
    `Path.stat()` for the staleness check, is the entire cost this
    function can ever add to the hook's hot path."""
    try:
        root = Path.cwd()  # same convention FilterRegistry(project_root=Path.cwd()) already uses above
        rel_path = _relative_posix_path(file_path, root)
        if rel_path is None:
            return rendered

        entries = intel_store.load_file_intelligence(root)
        if entries is None:
            return rendered
        entry = entries.get(rel_path)
        if entry is None:
            return rendered

        try:
            st = (root / rel_path).stat()
        except OSError:
            return rendered
        if st.st_size != entry.size or st.st_mtime_ns != entry.mtime_ns:
            return rendered  # stale cache entry — omit rather than show possibly-wrong info

        return _render_repo_context_block(rel_path, entry) + rendered
    except Exception as exc:  # noqa: BLE001 — fail-open: never let a lookup error surface
        warnings.warn(f"[quor] Repository Context lookup error: {exc}", stacklevel=2)
        return rendered


def _render_repo_context_block(rel_path: str, entry: FileIntelligenceEntry) -> str:
    defines = ", ".join(entry.top_symbols) if entry.top_symbols else "(none)"
    entry_point = "yes" if entry.entry_point else "no"
    return (
        f"Repository Context ({rel_path}):\n"
        f"  Kind: {entry.kind.capitalize()} | Language: {entry.language} | "
        f"Importance: {entry.importance} | Entry point: {entry_point}\n"
        f"  Defines: {defines}\n"
        f"  Imports: {entry.imports} file(s) | Imported by: {entry.imported_by} file(s)\n"
        "\n"
    )


# ---------------------------------------------------------------------------
# QB-081: Relevant repository files — deterministic query terms from the
# user's own most recent prompt text, fed through QB-080's `merge_search()`.
# ---------------------------------------------------------------------------

MAX_RELEVANT_FILES = 5
"""The one configuration constant this feature exposes (per QB-081's own
spec — no runtime flags yet): the maximum number of files the "Relevant
repository files" section ever shows, after every extracted query's
results have been merged and deduplicated. Within the ticket's own
suggested 3-8 range. Change this constant directly to tune it; there is no
CLI flag or config-file key for it yet."""

_TRANSCRIPT_TAIL_BYTES = 65536
"""Bound the transcript read to a fixed byte window from the end of the
file, regardless of total transcript size — a long-running session's
transcript can grow far larger than `file_intelligence.json` ever does,
and this feature must never let Read-hook latency scale with conversation
length. 64KiB comfortably holds the last several conversation turns in
practice; if the most recent user message happens to start before this
window, `_read_transcript_tail()` simply won't see it and this feature
silently finds nothing to search for — the same "omit rather than guess"
fail-open every other branch of this module already follows, not a
special case."""


def _read_transcript_tail(path: Path) -> str:
    """Read the last `_TRANSCRIPT_TAIL_BYTES` of `path` (or the whole file
    if smaller), decoding leniently (`errors="ignore"`) since a seek into
    the middle of a multi-byte UTF-8 sequence is expected, not exceptional.
    When the seek lands mid-line, that first (partial) line is dropped —
    it cannot be parsed as JSON anyway, and the real content it belonged to
    is either fully captured by a later, complete line or simply outside
    the window entirely. Returns `""` for any I/O failure (missing file,
    permission error, ...) — never raises; every caller already treats an
    empty return as "no transcript available."
    """
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with path.open("rb") as fh:
            if size > _TRANSCRIPT_TAIL_BYTES:
                fh.seek(size - _TRANSCRIPT_TAIL_BYTES)
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="ignore")
    if size > _TRANSCRIPT_TAIL_BYTES:
        _, _, text = text.partition("\n")
    return text


def _extract_last_user_prompt(transcript_path: str) -> str | None:
    """Best-effort scan of `transcript_path` (a JSONL conversation log
    Claude Code provides on every hook payload) for the most recent
    user-authored prompt text.

    **This is a deliberately isolated risk.** Claude Code's own
    documentation states the per-line transcript schema is internal and
    can change between releases — unlike `tool_input`/`tool_response`,
    which are the hook's actual documented contract. Every line is parsed
    defensively and any single malformed/unrecognized line is simply
    skipped, never raised; if Claude Code changes this format entirely,
    every line fails to yield text, this function returns `None`, and the
    whole QB-081 feature silently stops finding anything to inject —
    exactly the same "no cache, no message, no rebuild, no warning" fail-open
    contract QB-079's `_maybe_prepend_repo_context()` already follows for a
    missing/stale `file_intelligence.json`, applied here to a missing/stale
    prompt source instead.

    Tolerates two message shapes without raising on either: the real
    Claude Code transcript line (`{"type": "user", "message": {"role":
    "user", "content": [...]}, ...}`) and a flatter `{"role": "user",
    "content": ...}` shape, in case a future/alternate transcript writer
    uses the bare Anthropic Messages API shape directly. An explicit
    non-"user" `type` (assistant, tool result, summary/meta line, ...) is
    skipped; content blocks are flattened to their `"text"`-type entries
    only, so a message that is only tool-result content (no `"text"`
    block) contributes nothing, exactly as if it weren't user prompt text
    at all.
    """
    if not transcript_path:
        return None
    tail = _read_transcript_tail(Path(transcript_path))
    if not tail:
        return None

    last_text: str | None = None
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = orjson.loads(line)
        except orjson.JSONDecodeError:
            continue
        text = _extract_user_text(entry)
        if text:
            last_text = text
    return last_text


def _extract_user_text(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    entry_type = entry.get("type")
    if entry_type is not None and entry_type != "user":
        return None

    message = entry.get("message")
    if not isinstance(message, dict):
        message = entry  # tolerate a flatter {"role": ..., "content": ...} shape

    role = message.get("role")
    if role is not None and role != "user":
        return None

    return _flatten_content(message.get("content"))


def _flatten_content(content: Any) -> str | None:
    if isinstance(content, str):
        stripped = content.strip()
        return stripped or None
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        joined = "\n".join(parts).strip()
        return joined or None
    return None


def _maybe_prepend_relevant_files(hook_input: PostToolUseHookInput, base: str | None) -> str | None:
    """Prepend a "Relevant repository files" block onto `base` (whatever
    `_compress_read_output()` already produced, or the raw original
    `tool_response` when it produced nothing) if — and only if — every
    step below finds something real to inject; returns `None` the instant
    any step comes up empty (the caller, `_handle_text()`, treats `None`
    exactly like "this call added nothing," leaving whatever `compressed`
    already was untouched — never raises (mirrors
    `_maybe_prepend_repo_context()`'s own fail-open contract exactly,
    applied to an independent feature with its own cache-load and
    fail-open chain). Deliberately does **not** return `base` unchanged on
    a no-op: that would make `_handle_text()` treat an untouched original
    response as "genuinely compressed" and set `updatedToolOutput` to a
    byte-for-byte copy of content Claude Code already has — the same
    `None`-means-omit discipline `_compress_read_output()` itself already
    follows, applied one level up.

    Cheapest checks run first, deliberately, so a Read with nothing for
    this feature to do (no `transcript_path`, or a prompt with no
    identifier-shaped terms) never pays for a `file_intelligence.json`
    load at all — the hard "no slowdown when unavailable" requirement,
    satisfied by ordering rather than a separate short-circuit flag:
      1. `base` must be a string (nothing to prepend onto otherwise).
      2. `tool_input.file_path` must be non-empty.
      3. `transcript_path` must be non-empty.
      4. The bounded transcript tail read must yield some user prompt text.
      5. That text must yield at least one deterministic query term.
      Only once all five hold does this function touch
      `intel_store.load_file_intelligence()` — the same single O(1)-ish
      cache read `_maybe_prepend_repo_context()` already uses, requested
      independently here since the two features run on different branches
      (this one runs for every Read, including ones `_compress_read_output`
      never touches at all) and are kept decoupled on purpose (QB-081 must
      not risk regressing QB-079's already-shipped, already-tested
      behavior).
      6. `merge_search()` must return at least one match, excluding the
      file currently being read (recommending the file the agent already
      has the full contents of adds nothing).

    Any exception anywhere in this chain is caught and logged exactly like
    every other fail-open branch in this module; `None` is returned, same
    as every other early-exit above.
    """
    if base is None:
        return None
    file_path = hook_input.tool_input.file_path
    if not file_path:
        return None
    transcript_path = hook_input.transcript_path
    if not transcript_path:
        return None

    try:
        prompt_text = _extract_last_user_prompt(transcript_path)
        if not prompt_text:
            return None

        queries = extract_query_terms(prompt_text)
        if not queries:
            return None

        root = Path.cwd()
        rel_path = _relative_posix_path(file_path, root)
        exclude = frozenset({rel_path}) if rel_path is not None else frozenset()

        entries = intel_store.load_file_intelligence(root)
        if entries is None:
            return None

        matches = merge_search(entries, queries, limit=MAX_RELEVANT_FILES, exclude=exclude)
        if not matches:
            return None

        return render_relevant_files_block(matches) + base
    except Exception as exc:  # noqa: BLE001 — fail-open: never let this enhancement surface an error
        warnings.warn(f"[quor] Relevant repository files lookup error: {exc}", stacklevel=2)
        return None
