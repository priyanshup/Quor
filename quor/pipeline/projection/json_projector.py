"""json_projector: QB-119 JSON structural projection for tool payloads.

Strips null/empty-string fields and truncates long arrays to head+tail
elements plus an omitted-count placeholder, operating on the parsed JSON
*value* tree rather than raw text lines. This is deliberately NOT a
ContentMask stage: dropping an object key changes the surrounding
commas/brackets of whatever line it lived on, which ContentMask's
line-preservation contract (see `quor/pipeline/mask.py`'s module docstring
on which stages may rewrite a line, and how) has no room for — every
existing exception there either replaces one line with a summary or
rewrites a line's content in place, never removes a key from a JSON object
made of several lines. `quor.pipeline.structured_data_summarize` (QB-040)
sidesteps this by only ever collapsing whole array *elements* (never a key
inside a kept object) at line-range granularity; this module solves a
different problem — semantic field-level cleanup — so it runs as a
standalone pre-pass on the raw text, before that text is ever turned into
a ContentMask at all.

No built-in guessing at "boilerplate" values: `drop_pairs` is an optional,
caller-supplied set of exact key -> value matches to also strip (e.g.
{"status": "ok"}). Every removal this module performs on its own is a
zero-information-loss default (an empty/null field carries no signal; an
omitted array element's count is preserved in the placeholder) or an exact
match the caller explicitly declared — never a hardcoded heuristic about
what a "standard success" value looks like across arbitrary APIs.

Fail-open: text that isn't valid JSON (or a cleaned value `json.dumps`
can't re-serialize) is returned unchanged.
"""

from __future__ import annotations

import json
from typing import Any

_OMITTED_KEY = "__omitted_items__"

DEFAULT_HEAD = 3
DEFAULT_TAIL = 2


def project_json_text(
    text: str,
    *,
    drop_pairs: dict[str, Any] | None = None,
    head: int = DEFAULT_HEAD,
    tail: int = DEFAULT_TAIL,
) -> str:
    """Clean and re-serialize `text` if it parses as a JSON object/array;
    otherwise return it unchanged. See module docstring for the exact
    cleaning rules."""
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return text  # cheap skip: never worth attempting a JSON parse

    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return text

    cleaned = _clean(value, drop_pairs, head, tail)

    try:
        return json.dumps(cleaned, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return text


def _clean(value: Any, drop_pairs: dict[str, Any] | None, head: int, tail: int) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, val in value.items():
            if val is None or val == "":
                continue
            if drop_pairs is not None and key in drop_pairs and val == drop_pairs[key]:
                continue
            cleaned[key] = _clean(val, drop_pairs, head, tail)
        return cleaned

    if isinstance(value, list):
        cleaned_list = [_clean(item, drop_pairs, head, tail) for item in value]
        omitted = len(cleaned_list) - head - tail
        if omitted <= 0:
            return cleaned_list
        placeholder = {_OMITTED_KEY: f"{omitted} items hidden"}
        tail_part = cleaned_list[len(cleaned_list) - tail :] if tail > 0 else []
        return [*cleaned_list[:head], placeholder, *tail_part]

    return value
