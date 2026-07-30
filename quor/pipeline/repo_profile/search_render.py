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

from quor.pipeline.repo_profile.search_model import SearchEvidence, SearchMatch, SearchResult

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


_RELEVANT_FILES_LABELS: dict[SearchEvidence, str] = {
    "exact_symbol": "Exact symbol",
    "exact_filename": "Exact filename",
    "exact_directory": "Exact directory",
    "prefix_symbol": "Symbol prefix",
    "filename_contains": "Filename contains",
    "top_symbol": "Symbol",
    "dependency": "Dependency",
}
"""QB-081's own, deliberately shorter label set for `render_relevant_files_block()`
below — distinct from `_EVIDENCE_LABELS` above (`"Exact symbol match"` etc.),
which is `quor search`'s own fuller CLI wording. The Read hook prepends its
block onto real file content on every matching Read call, so each entry
stays to one label word plus the matched value, not a full sentence."""


def render_relevant_files_block(matches: list[SearchMatch]) -> str:
    """QB-081's Read-hook injection format: a compact "Relevant repository
    files" section, one path plus one evidence line per match, no scores
    or confidence — deliberately distinct from `render_search_text()`'s
    fuller per-match template (Evidence/Matched/Language/Kind/Importance/
    Imports/Imported-by), which is built for a one-shot `quor search`
    invocation a person reads, not something prepended onto every Read.

    Ends with exactly one blank line after the last entry (mirrors
    `claude_read._render_repo_context_block()`'s own trailing blank-line
    separator) so the caller can prepend this directly onto real file
    content with no extra join logic. Returns `""` for an empty `matches`
    list — `claude_read.py` never calls this with an empty list (it only
    calls this after confirming `matches` is non-empty), but keeping this
    function total rather than assuming a non-empty caller avoids a
    silent one-line join bug if that ever changes.
    """
    if not matches:
        return ""
    lines = ["Relevant repository files", ""]
    for m in matches:
        lines.append(f"- {m.path}")
        lines.append(f"  {_RELEVANT_FILES_LABELS[m.evidence]}: {m.matched_value}")
        lines.append("")
    return "\n".join(lines) + "\n"
