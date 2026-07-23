"""JSON analyzer for the structured-data summarization framework (QB-040).

Public API: `analyze_json(source: str) -> list[CollapseRange]`.

No third-party dependency, no hand-rolled JSON grammar: every value is
still decoded by the stdlib `json` module's own `JSONDecoder.raw_decode`
(which correctly handles string escaping, numbers, unicode, etc.) — this
module only adds *position tracking* on top, by driving `raw_decode` itself
one value at a time instead of calling `json.loads` once. Between two
`raw_decode` calls at the same nesting level, the only characters that can
legally appear are whitespace, `,`, `:`, or a container's closing bracket
(`]`/`}`) — never a string, number, or nested bracket, because `raw_decode`
already consumed the entirety of the previous value, including anything
bracket-like inside a string. This is what makes plain forward scanning
between calls safe without re-implementing JSON's escaping rules.

Fail-open contract: mirrors `quor/pipeline/ast_summarize/python.py` exactly
(the same "opposite of `quor/pipeline/extract`" contract) — a malformed-JSON
`json.JSONDecodeError` (or `IndexError`/`ValueError` from this module's own
boundary scanning on truncated/invalid input) is NOT caught here. It
propagates to `Pipeline.execute()`'s existing per-stage fail-open handling.
"""

from __future__ import annotations

import json
from typing import Any

from quor.pipeline.structured_data.collapse import (
    DEFAULT_KEEP_HEAD,
    DEFAULT_MAX_ITEMS,
    CollapseRange,
    is_homogeneous,
    summary_line,
)

_decoder = json.JSONDecoder()
_WHITESPACE = " \t\n\r"


def analyze_json(source: str) -> list[CollapseRange]:
    """Return collapse ranges for every homogeneous array in `source` longer
    than `DEFAULT_MAX_ITEMS` elements, at any nesting depth.

    Raises `json.JSONDecodeError` (or `ValueError`/`IndexError` from this
    module's own scanning) on malformed input — not caught here, see module
    docstring "Fail-open contract".
    """
    ranges: list[CollapseRange] = []
    i = _skip_ws(source, 0)
    if i >= len(source):
        return ranges
    _walk(source, i, ranges)
    return ranges


def _line_of(source: str, offset: int) -> int:
    """1-indexed line number containing character `source[offset]`."""
    return source.count("\n", 0, offset) + 1


def _skip_ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in _WHITESPACE:
        i += 1
    return i


def _walk(s: str, i: int, ranges: list[CollapseRange]) -> tuple[Any, int]:
    """Parse the JSON value starting at `s[i]`. Returns (value, end_offset).

    Recurses into arrays/objects itself (rather than delegating to
    `raw_decode` for containers) so every element's own start/end offset is
    always known — `raw_decode` alone only ever gives the *whole* value's
    end offset, not each of its children's.
    """
    i = _skip_ws(s, i)
    ch = s[i]
    if ch == "[":
        return _walk_array(s, i, ranges)
    if ch == "{":
        return _walk_object(s, i, ranges)
    return _decoder.raw_decode(s, i)


def _walk_array(s: str, i: int, ranges: list[CollapseRange]) -> tuple[list[Any], int]:
    i += 1  # skip '['
    i = _skip_ws(s, i)
    elems: list[tuple[int, int, Any, list[CollapseRange]]] = []

    if i < len(s) and s[i] == "]":
        return [], i + 1

    while True:
        i = _skip_ws(s, i)
        elem_start = i
        sub_ranges: list[CollapseRange] = []
        value, elem_end = _walk(s, i, sub_ranges)
        elems.append((elem_start, elem_end, value, sub_ranges))
        i = _skip_ws(s, elem_end)
        if s[i] == ",":
            i += 1
            continue
        if s[i] == "]":
            i += 1
            break
        raise ValueError(f"malformed JSON array: expected ',' or ']' at offset {i}")

    close_line = _line_of(s, i - 1)
    collapsed = _maybe_collapse(s, elems, close_line, ranges, unit="item")
    if not collapsed:
        for _, _, _, sub in elems:
            ranges.extend(sub)

    return [e[2] for e in elems], i


def _walk_object(s: str, i: int, ranges: list[CollapseRange]) -> tuple[dict[str, Any], int]:
    i += 1  # skip '{'
    i = _skip_ws(s, i)
    result: dict[str, Any] = {}

    if i < len(s) and s[i] == "}":
        return result, i + 1

    while True:
        i = _skip_ws(s, i)
        key, i = _decoder.raw_decode(s, i)
        i = _skip_ws(s, i)
        if s[i] != ":":
            raise ValueError(f"malformed JSON object: expected ':' at offset {i}")
        i = _skip_ws(s, i + 1)
        value, i = _walk(s, i, ranges)
        result[str(key)] = value
        i = _skip_ws(s, i)
        if s[i] == ",":
            i += 1
            continue
        if s[i] == "}":
            i += 1
            break
        raise ValueError(f"malformed JSON object: expected ',' or '}}' at offset {i}")

    return result, i


def _maybe_collapse(
    s: str,
    elems: list[tuple[int, int, Any, list[CollapseRange]]],
    close_line: int,
    ranges: list[CollapseRange],
    *,
    unit: str,
) -> bool:
    """If `elems` qualifies for collapsing, append the summary CollapseRange
    (plus kept elements' own sub-ranges) to `ranges` and return True.
    Returns False (nothing appended) if it doesn't qualify — the caller is
    responsible for extending `ranges` with every element's sub-ranges in
    that case."""
    values = [e[2] for e in elems]
    if len(values) <= DEFAULT_MAX_ITEMS or not is_homogeneous(values):
        return False

    keep = elems[:DEFAULT_KEEP_HEAD]
    omitted = elems[DEFAULT_KEEP_HEAD:]

    keep_end_line = _line_of(s, keep[-1][1] - 1)
    omitted_start_line = _line_of(s, omitted[0][0])
    omitted_end_line = _line_of(s, omitted[-1][1] - 1)

    # Safety: never collapse if a kept element shares a line with the first
    # omitted element (compressing that line would delete kept content), or
    # if the container's own closing bracket shares a line with the last
    # omitted element (compressing that line would delete the bracket).
    # "When uncertain, keep it" — same rule cat.toml's own comments cite.
    if omitted_start_line <= keep_end_line or close_line <= omitted_end_line:
        return False

    for _, _, _, sub in keep:
        ranges.extend(sub)

    ranges.append(
        CollapseRange(
            compress_start=omitted_start_line,
            compress_end=omitted_end_line,
            summary=summary_line(len(omitted), len(values), unit=unit),
        )
    )
    return True
