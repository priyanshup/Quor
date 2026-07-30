"""`quor search` -> plain-text and JSON presentation (QB-080).

Plain, deterministic text by default (not Rich tables) — mirrors
`explorer_render.py`'s own reasoning: a fixed, grep-friendly template reads
equally well pasted into an AI's context or read directly by a person.
Missing/corrupted-cache rendering is reused directly from
`explorer_render.py` (`render_cache_unavailable_text/json`) rather than
duplicated — `quor search`'s `CacheUnavailable` shape is identical to `quor
explore`'s. `--json` uses the same `dataclasses.asdict()` + `orjson` idiom
every other repository-intelligence command's JSON mode uses.

Every match shows a **fixed** field set — Evidence, Matched, Language, Kind,
Importance, Imports, Imported by — deliberately not the varying-fields-per-
match shown in QB-080's own illustrative ticket example, for predictable,
testable output consistent with every other repo-profile renderer's
fixed-template convention. "Evidence" (not "Score") because there is no
numeric score anywhere in this design, only a named tier — the label says
exactly what it is. "Matched" is deliberately prominent, not buried, since
it's the concrete answer to "why did this show up."
"""

from __future__ import annotations

import dataclasses

import orjson

from quor.pipeline.repo_profile.search_model import SearchEvidence, SearchResult

_EVIDENCE_LABELS: dict[SearchEvidence, str] = {
    "exact_symbol": "Exact symbol match",
    "exact_filename": "Exact filename match",
    "exact_directory": "Exact directory match",
    "prefix_symbol": "Prefix symbol match",
    "filename_contains": "Filename contains query",
    "top_symbol": "Top-symbol match",
    "dependency": "Dependency match",
}


def render_search_json(result: SearchResult) -> str:
    return orjson.dumps(dataclasses.asdict(result), option=orjson.OPT_INDENT_2).decode()


def render_search_text(result: SearchResult) -> str:
    if not result.matches:
        return f'No matches found for "{result.query}".'

    lines = ["Top matches", ""]
    for i, m in enumerate(result.matches, start=1):
        lines.extend(
            [
                f"{i}. {m.path}",
                f"   Evidence: {_EVIDENCE_LABELS[m.evidence]}",
                f"   Matched: {m.matched_value}",
                f"   Language: {m.language}",
                f"   Kind: {m.kind.capitalize()}",
                f"   Importance: {m.importance}",
                f"   Imports: {m.imports}",
                f"   Imported by: {m.imported_by}",
                "",
            ]
        )
    lines.pop()  # drop the trailing blank line after the last match

    if result.truncated:
        lines.append(
            f"Showing {len(result.matches)} of {result.total_candidates} matches (--limit)."
        )
    else:
        lines.append(f"Total: {result.total_candidates}")

    return "\n".join(lines)
