"""QB-034: `quor discover` — retroactively scan a project's existing Claude
Code session transcripts and report what Quor would have saved on `Bash`
commands it never had a chance to compress.

**Why this is safe against Quor's own anti-goals.** `ANTI_GOALS.md` #4
("never store, transmit, or log command output content") and #5 ("never
implement telemetry... without explicit opt-in") both govern what *Quor*
does with content it processes. Neither is implicated here: this module only
*reads* session transcripts Claude Code itself already wrote to the user's
own local disk (`~/.claude/projects/`), computes a report in memory, prints
it, and exits — nothing scanned here is written to `TrackingDB` (which
architecturally never stores content at all, see QB-047's own investigation)
or anywhere else Quor persists state. No network call is made. This is a
manually-invoked, read-only, single-run command — never a background
process, never automatic.

**Where Claude Code stores sessions is not documented as a stable public
API.** The same caution QB-081's now-removed `_extract_last_user_prompt()`
applied to the transcript *line* schema applies here to the *directory*
location: best-effort, fail-open. Rather than reverse-engineering Claude
Code's project-directory name-sanitization scheme (fragile — see this
module's own tests for why a naive re-derivation was rejected), every
session file's own recorded `cwd` field is read directly and compared
against the real project path — exact, not guessed, and immune to a future
change in how Claude Code names project directories.

Uses `Path.home()`, not `platformdirs` — deliberately: `platformdirs`
resolves *Quor's own* per-OS config/data conventions (see `docs/final/
CLAUDE.md`'s coding conventions); `~/.claude/` is a fixed, cross-platform
location a *different* application (Claude Code) already chose for itself,
which Quor is only ever reading from, never writing.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import orjson

from quor.filters.registry import FilterRegistry
from quor.pipeline.content_type import detect
from quor.tracking.db import count_tokens

# How many leading lines of a session file to inspect for a `cwd` match
# before giving up on that file — `cwd` is written on nearly every line in
# practice (confirmed against real session data), so a small bound keeps a
# non-matching, unrelated-project session file cheap to skip.
_CWD_PROBE_LINES = 20

# A tool name is treated as "this content was already compressed by Quor"
# only if it contains this substring — matches Quor's own MCP tool
# regardless of the exact server registration name a user's client shows it
# under (e.g. `mcp__quor__compress_context`), without hardcoding one exact
# qualified name.
_COMPRESS_TOOL_MARKER = "compress_context"


@dataclass(frozen=True)
class UncoveredCommand:
    """One `Bash` invocation Quor never compressed, with what it would have
    saved had it run through the real filter pipeline."""

    description: str
    command_verb: str
    matched_filter: str | None
    original_tokens: int
    retroactive_tokens: int

    @property
    def tokens_would_save(self) -> int:
        return self.original_tokens - self.retroactive_tokens


@dataclass(frozen=True)
class FilterAggregate:
    count: int
    original_tokens: int
    tokens_would_save: int


@dataclass(frozen=True)
class DiscoverReport:
    sessions_scanned: int
    commands_scanned: int
    commands_already_covered: int
    total_original_tokens: int
    total_tokens_would_save: int
    by_filter: dict[str, FilterAggregate]
    top_commands: tuple[UncoveredCommand, ...]

    @property
    def would_save_pct(self) -> float:
        if self.total_original_tokens == 0:
            return 0.0
        return self.total_tokens_would_save / self.total_original_tokens * 100


@dataclass(frozen=True)
class _RawBashInvocation:
    description: str
    command: str
    stdout: str
    already_covered: bool


def find_session_files(
    project_root: Path, *, claude_home: Path | None = None, days: int = 30
) -> list[Path]:
    """Every `.jsonl` session transcript, across all of Claude Code's project
    directories, whose own recorded `cwd` matches `project_root` and whose
    mtime falls within the last `days` days. Fails open to an empty list —
    a missing/unreadable `~/.claude/projects/` is "no sessions found," never
    an error, matching every other best-effort feature built on Claude
    Code's own file formats (QB-081's precedent)."""
    claude_home = claude_home or Path.home() / ".claude"
    projects_dir = claude_home / "projects"
    try:
        if not projects_dir.is_dir():
            return []
        candidate_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]
    except OSError:
        return []

    cutoff = time.time() - days * 86400 if days > 0 else None

    resolved_root = _normalize_path(project_root)
    matches: list[Path] = []
    for project_dir in candidate_dirs:
        try:
            jsonl_files = list(project_dir.glob("*.jsonl"))
        except OSError:
            continue
        for jsonl_file in jsonl_files:
            try:
                if cutoff is not None and jsonl_file.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            if _session_matches_project(jsonl_file, resolved_root):
                matches.append(jsonl_file)
    return matches


def _normalize_path(path: Path) -> str:
    """Case-insensitive, resolved comparison key — Windows paths differ in
    drive-letter/segment case between how a user's shell reports `cwd` and
    how Claude Code recorded it (confirmed against real session data: `C:\\
    Users\\...` vs. recorded `c:\\Users\\...`), and NTFS is itself
    case-insensitive, so a case-sensitive comparison would under-match."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return os.path.normcase(str(resolved))


def _session_matches_project(jsonl_file: Path, resolved_root: str) -> bool:
    try:
        with jsonl_file.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _CWD_PROBE_LINES:
                    return False
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue
                cwd = obj.get("cwd") if isinstance(obj, dict) else None
                if isinstance(cwd, str) and _normalize_path(Path(cwd)) == resolved_root:
                    return True
    except OSError:
        return False
    return False


def _iter_json_lines(jsonl_file: Path) -> Iterator[dict[str, object]]:
    try:
        f = jsonl_file.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _collect_already_compressed(jsonl_file: Path) -> set[str]:
    """First pass: every `raw_text` ever passed to a Quor
    `compress_context`-named tool call in this session, regardless of where
    in the file it appears. A `compress_context` call always comes *after*
    the `Bash` result it compresses (the assistant has to see the raw output
    before deciding to compress it) — so this must be a full pre-pass, not
    something a single forward streaming pass over `_iter_bash_invocations`
    could ever catch by the time it reaches that Bash result's own line."""
    already_compressed: set[str] = set()
    for obj in _iter_json_lines(jsonl_file):
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if _COMPRESS_TOOL_MARKER not in str(block.get("name", "")):
                continue
            tool_input = block.get("input")
            raw_text = tool_input.get("raw_text") if isinstance(tool_input, dict) else None
            if isinstance(raw_text, str):
                already_compressed.add(raw_text)
    return already_compressed


def _iter_bash_invocations(jsonl_file: Path) -> Iterator[_RawBashInvocation]:
    """Pair each `Bash` `tool_use` block with its later `tool_result` via
    `tool_use_id`, flagging any whose raw stdout exactly matches text already
    passed to a Quor `compress_context` call elsewhere in the same session
    (exact match only, no fuzzy comparison, per this project's own
    no-fuzzy-matching convention). Two passes over the file (see
    `_collect_already_compressed`'s own docstring for why one pass can't
    work) — each pass still streams line by line, never loading the whole
    file into memory at once, so this remains safe for the multi-MB
    transcripts real sessions produce."""
    already_compressed = _collect_already_compressed(jsonl_file)
    pending: dict[str, tuple[str, str]] = {}

    for obj in _iter_json_lines(jsonl_file):
        message = obj.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use" and block.get("name") == "Bash":
                tool_input = block.get("input")
                if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
                    tool_id = block.get("id")
                    if isinstance(tool_id, str):
                        pending[tool_id] = (
                            str(tool_input.get("description", "")),
                            tool_input["command"],
                        )
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if not isinstance(tool_use_id, str) or tool_use_id not in pending:
                    continue
                description, command = pending.pop(tool_use_id)
                stdout = _extract_stdout(obj, block)
                if stdout:
                    yield _RawBashInvocation(
                        description, command, stdout, stdout in already_compressed
                    )


def _extract_stdout(line_obj: dict[str, object], tool_result_block: dict[str, object]) -> str:
    """Prefer `toolUseResult.stdout` (the raw capture Claude Code itself
    records, matching exactly what `quor.engine.dispatcher` filters today —
    stdout only, stderr is never captured/filtered, per that module's own
    `stderr=None` inherit-to-terminal design) over the `tool_result` content
    string, which may already carry model-facing formatting differences."""
    tool_use_result = line_obj.get("toolUseResult")
    if isinstance(tool_use_result, dict):
        stdout = tool_use_result.get("stdout")
        if isinstance(stdout, str):
            return stdout
    content = tool_result_block.get("content")
    return content if isinstance(content, str) else ""


def _command_verb(command: str) -> str:
    stripped = command.strip()
    if not stripped:
        return "(empty)"
    return stripped.split()[0]


def scan_project(
    project_root: Path,
    *,
    days: int = 30,
    claude_home: Path | None = None,
    top_n: int = 10,
) -> DiscoverReport:
    """The real entry point: find this project's session transcripts, score
    every `Bash` invocation Quor never compressed against the real filter
    pipeline, and return an aggregate report. Every number is computed
    fresh on each call — nothing here is cached or persisted."""
    session_files = find_session_files(project_root, claude_home=claude_home, days=days)
    registry = FilterRegistry(project_root=project_root)

    commands_scanned = 0
    commands_already_covered = 0
    total_original = 0
    total_would_save = 0
    by_filter: dict[str, list[int]] = {}
    scored: list[UncoveredCommand] = []

    for session_file in session_files:
        for invocation in _iter_bash_invocations(session_file):
            commands_scanned += 1
            if invocation.already_covered:
                commands_already_covered += 1
                continue
            scored_command = _score_invocation(invocation, registry)
            total_original += scored_command.original_tokens
            total_would_save += scored_command.tokens_would_save

            key = scored_command.matched_filter or "(no filter)"
            bucket = by_filter.setdefault(key, [0, 0, 0])
            bucket[0] += 1
            bucket[1] += scored_command.original_tokens
            bucket[2] += scored_command.tokens_would_save

            scored.append(scored_command)

    scored.sort(key=lambda c: c.tokens_would_save, reverse=True)

    return DiscoverReport(
        sessions_scanned=len(session_files),
        commands_scanned=commands_scanned,
        commands_already_covered=commands_already_covered,
        total_original_tokens=total_original,
        total_tokens_would_save=total_would_save,
        by_filter={
            name: FilterAggregate(count=c, original_tokens=o, tokens_would_save=s)
            for name, (c, o, s) in by_filter.items()
        },
        top_commands=tuple(scored[:top_n]),
    )


def _score_invocation(
    invocation: _RawBashInvocation, registry: FilterRegistry
) -> UncoveredCommand:
    original_tokens = count_tokens(invocation.stdout)
    filter_config = registry.find(invocation.command)
    if filter_config is None:
        retroactive_tokens = original_tokens
        matched_filter = None
    else:
        content_type = detect(invocation.stdout).value
        compressed = registry.apply(filter_config, invocation.stdout, content_type=content_type)
        retroactive_tokens = count_tokens(compressed)
        matched_filter = filter_config.name

    description = invocation.description.strip() or _command_verb(invocation.command)
    return UncoveredCommand(
        description=description,
        command_verb=_command_verb(invocation.command),
        matched_filter=matched_filter,
        original_tokens=original_tokens,
        retroactive_tokens=retroactive_tokens,
    )
