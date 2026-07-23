"""TOML analyzer for the structured-data summarization framework (QB-040).

Public API: `analyze_toml(source: str) -> list[CollapseRange]`.

Scope, deliberately narrow: TOML's stdlib parser (`tomllib`, read-only) has
no position-tracking API at all — unlike `json.raw_decode` (json_fmt.py) or
PyYAML's `compose()` (yaml_fmt.py), there is nothing in the standard library
to drive for per-value line numbers. Rather than hand-rolling a full,
position-aware TOML tokenizer (TOML's grammar is considerably larger than
JSON's — multiple string/array/inline-table syntaxes, dotted keys, several
date/time formats), this module targets the one construct that accounts for
essentially every real-world "hundreds of near-identical entries" TOML file
QB-040 is meant to help with: **array-of-tables** (`[[name]]` blocks) — the
exact shape `poetry.lock`/`Cargo.lock`/`pdm.lock`-style lockfiles use for
their dependency lists. A `[[name]]` (or `[name]`) header is, by TOML's own
grammar, always a complete, standalone line at column 0 — so finding these
headers is exact line-level pattern matching on the format's own syntax,
not a heuristic guess, even though the values *inside* each block are never
individually parsed for position.

**Known limitation** (documented, not silently absent): inline arrays
(`deps = ["a", "b", "c", ...]`) are never collapsed — only array-of-tables.
In practice this is the right trade-off: hand-written TOML's inline arrays
(`pyproject.toml` direct dependency lists) are rarely pathologically long;
the genuinely large, repetitive lists live in generated lockfiles, which use
array-of-tables. A run of `[[name]]` headers is also only treated as one
array if the headers are *exactly consecutive* in the file (no other table
header — e.g. a `[name.sub]` per-entry header some other lockfile format
might use — interleaved between them); an interleaved run simply isn't
collapsed (safe: no compression opportunity is taken, nothing is ever
misread). Quoted-key headers (`[["pkg name"]]`) are not recognized (bare
dotted keys only) — real-world lockfiles do not use them.

Fail-open contract: mirrors `json_fmt.py`/`yaml_fmt.py` — a genuine parse
failure (`tomllib.TOMLDecodeError`) propagates, uncaught. `tomllib` is
always available (stdlib, Python 3.11+) — there is no "optional dependency
missing" case for this analyzer, unlike `yaml_fmt.py`'s.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

from quor.pipeline.structured_data.collapse import (
    DEFAULT_KEEP_HEAD,
    DEFAULT_MAX_ITEMS,
    CollapseRange,
    is_homogeneous,
)

_BARE_DOTTED_KEY = r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
_ARRAY_HEADER_RE = re.compile(rf"^\[\[({_BARE_DOTTED_KEY})\]\]\s*(?:#.*)?$")
_TABLE_HEADER_RE = re.compile(rf"^\[({_BARE_DOTTED_KEY})\]\s*(?:#.*)?$")


def analyze_toml(source: str) -> list[CollapseRange]:
    """Return collapse ranges for every homogeneous array-of-tables run in
    `source` with more than `DEFAULT_MAX_ITEMS` blocks. See module docstring
    for scope (array-of-tables only, strictly consecutive headers only).

    Raises `tomllib.TOMLDecodeError` on malformed TOML — not caught here,
    see module docstring "Fail-open contract".
    """
    parsed = tomllib.loads(source)
    lines = source.split("\n")
    headers = _find_headers(lines)

    ranges: list[CollapseRange] = []
    i = 0
    n = len(headers)
    while i < n:
        _line_no, name, is_array = headers[i]
        if not is_array:
            i += 1
            continue

        run_start = i
        j = i + 1
        while j < n and headers[j][1] == name and headers[j][2]:
            j += 1
        run = headers[run_start:j]
        i = j

        collapse_range = _maybe_collapse_run(parsed, lines, headers, run_start, run)
        if collapse_range is not None:
            ranges.append(collapse_range)

    return ranges


def _find_headers(lines: list[str]) -> list[tuple[int, str, bool]]:
    """Return every `[name]`/`[[name]]` header line as (1-indexed line
    number, dotted name, is_array_of_tables), in file order."""
    headers: list[tuple[int, str, bool]] = []
    for idx, line in enumerate(lines):
        m = _ARRAY_HEADER_RE.match(line)
        if m:
            headers.append((idx + 1, m.group(1), True))
            continue
        m = _TABLE_HEADER_RE.match(line)
        if m:
            headers.append((idx + 1, m.group(1), False))
    return headers


def _lookup_array(parsed: dict[str, Any], dotted_name: str) -> list[Any] | None:
    node: Any = parsed
    for part in dotted_name.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, list) else None


def _maybe_collapse_run(
    parsed: dict[str, Any],
    lines: list[str],
    headers: list[tuple[int, str, bool]],
    run_start: int,
    run: list[tuple[int, str, bool]],
) -> CollapseRange | None:
    block_count = len(run)
    if block_count <= DEFAULT_MAX_ITEMS:
        return None

    name = run[0][1]
    values = _lookup_array(parsed, name)
    if values is None or len(values) != block_count or not is_homogeneous(values):
        return None

    total_lines = len(lines)
    last_block_idx = run_start + block_count - 1
    if last_block_idx + 1 < len(headers):
        last_block_end = headers[last_block_idx + 1][0] - 1
    else:
        last_block_end = total_lines

    omitted_start_line = run[DEFAULT_KEEP_HEAD][0]
    omitted_end_line = last_block_end
    # Trim trailing blank lines so the summary doesn't absorb a blank
    # separator that visually belongs after the array, not inside it.
    while omitted_end_line > omitted_start_line and not lines[omitted_end_line - 1].strip():
        omitted_end_line -= 1

    omitted_count = block_count - DEFAULT_KEEP_HEAD
    plural = "ies" if omitted_count != 1 else "y"
    summary = (
        f"... {omitted_count} more [[{name}]] entr{plural} omitted "
        f"({block_count} total) ..."
    )
    return CollapseRange(
        compress_start=omitted_start_line, compress_end=omitted_end_line, summary=summary
    )
