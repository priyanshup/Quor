"""Shared collapse primitives for structured-data summarization (QB-040).

`CollapseRange` is the common output shape every per-format analyzer
(`json_fmt.py`, `yaml_fmt.py`, `toml_fmt.py`) produces: a 1-indexed,
line-based instruction the `structured_data_summarize` stage applies
directly to a `ContentMask` — line `compress_start`'s text is replaced with
`summary` (kept, per `group_repeated`'s "reuse one line as the placeholder"
convention — see `quor/pipeline/mask.py`'s module docstring on which stages
may rewrite a line), and lines `compress_start + 1 .. compress_end` are
marked COMPRESS. Every other line — including every KEPT array element — is
untouched, byte-for-byte identical to the original file.

`_is_homogeneous`/`_shape_key` implement the one deterministic rule every
analyzer uses to decide whether a long array/sequence is safe to collapse:
every element must share the same *shape* — same JSON/YAML/TOML type, and
for objects/mappings, the exact same set of keys. This is a structural
equality check, not a heuristic guess at "importance" — a list of `{"name":
..., "version": ...}` dependency records collapses; a list mixing strings,
numbers, and differently-shaped objects never does, so no genuinely distinct
value is ever silently dropped (QB-040's "preserve keys/schema shape and any
genuinely distinct values" requirement).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# An array/sequence/array-of-tables only becomes a collapse candidate once it
# has more than this many elements...
DEFAULT_MAX_ITEMS = 6
# ...in which case the first this-many elements are kept in full (recursively
# summarized) and the rest are replaced by one placeholder line.
DEFAULT_KEEP_HEAD = 3


@dataclass(frozen=True)
class CollapseRange:
    """One collapsed run, in 1-indexed source line numbers (both inclusive).

    `compress_start` is always < the original array/sequence/table-array's
    last line and is the line whose text is replaced with `summary`.
    `compress_start == compress_end` is valid (the omitted run fits on the
    one line being replaced; nothing extra to mark COMPRESS).
    """

    compress_start: int
    compress_end: int
    summary: str


def summary_line(omitted_count: int, total_count: int, *, unit: str = "item") -> str:
    """Build the one placeholder line's text for an omitted run.

    Shared verbatim by all three format analyzers so the compressed output
    reads consistently regardless of source format.
    """
    plural = "s" if omitted_count != 1 else ""
    return f"... {omitted_count} more {unit}{plural} omitted ({total_count} total) ..."


def _shape_key(value: Any) -> tuple[str, frozenset[str] | None]:
    """Return a (type_name, key_set_or_None) pair describing `value`'s shape.

    `bool` is checked before `int`/`float` since `bool` is a subclass of
    `int` in Python — `True`/`False` must never be considered the same shape
    as a plain number.
    """
    if isinstance(value, bool):
        return ("bool", None)
    if isinstance(value, dict):
        return ("object", frozenset(value.keys()))
    if isinstance(value, list):
        return ("array", None)
    if isinstance(value, (int, float)):
        return ("number", None)
    if isinstance(value, str):
        return ("string", None)
    if value is None:
        return ("null", None)
    return ("other", None)


def is_homogeneous(values: list[Any]) -> bool:
    """True if every element of `values` shares the same shape (see
    `_shape_key`). Empty and single-element lists are never "homogeneous" in
    the sense this module cares about — there is nothing to collapse."""
    return is_homogeneous_shapes([_shape_key(v) for v in values])


def is_homogeneous_shapes(shapes: list[tuple[str, frozenset[str] | None]]) -> bool:
    """True if every entry in `shapes` is identical to the first.

    The format-agnostic half of `is_homogeneous()`: `json_fmt.py` derives
    shapes from real Python values via `_shape_key`; `yaml_fmt.py` derives
    them directly from PyYAML `Node.tag`/mapping keys instead (no need to
    fully resolve each node's Python value just to compare shapes) but
    shares this exact same "all equal" comparison rather than duplicating
    it.
    """
    if len(shapes) < 2:
        return False
    first = shapes[0]
    return all(s == first for s in shapes[1:])
