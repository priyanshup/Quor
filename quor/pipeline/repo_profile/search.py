"""Semantic Repository Search — cache-only lookups for `quor search` (QB-080).

**This module never walks, parses, rebuilds, or calls `ensure_repo_intelligence()`.**
Its sole read path is `intel_store.load_file_intelligence()` — one file,
`file_intelligence.json` — never `symbol_facts.json`/`graph_facts.json`.
Those two were measured (QB-079's own investigation) at 114ms combined for
this repository's 469 files, an O(repo-size) cost this command must never
pay: `quor explore find`/`deps`/`used-by` already pay it, but as an explicit
one-shot exhaustive query, not something meant to scale the way a general
search command must. `file_intelligence.json`'s full-dict load is proven
flat and cheap instead (<50ms CPU even at 2000 files —
`tests/unit/test_repo_intel_benchmark.py::TestFileIntelligenceLookup`), so
every evidence tier below is deliberately built from *only* what that one
cache already carries.

Ranking is entirely deterministic — no embeddings, no fuzzy/TF-IDF scoring,
no randomness. `search()` scores every candidate file into exactly one of
seven evidence tiers (`search_model.SearchEvidence`), in a fixed priority
order, then sorts by a fully-specified tuple. See `_best_evidence()` for the
tier definitions and `_sort_key()` for the ranking.

**Dependency matching (tier 7) is deliberately file-level only.** It is
built entirely from `FileIntelligenceEntry.imported_files` (a resolved
`import`-kind edge, QB-080) and this module's own in-memory reverse index —
never a call/inherit/export edge, never a symbol name, and never a load of
`symbol_facts.json`/`graph_facts.json`. Do not "improve" this into
symbol-level detail; that would silently reintroduce the exact O(repo-size)
cost this whole module exists to avoid.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.explorer_model import CacheUnavailable
from quor.pipeline.repo_profile.intel_model import FileImportance, FileIntelligenceEntry, FileKind
from quor.pipeline.repo_profile.search_model import SearchEvidence, SearchMatch, SearchResult

MISSING_MESSAGE = (
    "No repository intelligence found for this repository. Run `quor map` "
    "(or `quor symbols` / `quor graph` / `quor repo`) once to build it — "
    "`quor search` only reads that cache, it never builds one itself."
)
CORRUPTED_MESSAGE = (
    "The repository intelligence cache exists but could not be read. Run "
    "`quor repo --rebuild` (or `quor map --rebuild`) to rebuild it — `quor "
    "search` only reads the cache, it never rebuilds one itself."
)

_DEFAULT_LIMIT = 20

_EVIDENCE_PRIORITY: dict[SearchEvidence, int] = {
    "exact_symbol": 0,
    "exact_filename": 1,
    "exact_directory": 2,
    "prefix_symbol": 3,
    "filename_contains": 4,
    "top_symbol": 5,
    "dependency": 6,
}
"""Descending-priority rank for each of the 7 evidence tiers (0 = highest) —
this command's own ranking contract, per QB-080's own spec."""

_IMPORTANCE_RANK: dict[FileImportance, int] = {"High": 0, "Medium": 1, "Low": 2}

_FILENAME_EVIDENCE = frozenset[SearchEvidence]({"exact_filename", "filename_contains"})
"""The only two tiers the filename-length tiebreak applies to — filename
length has no bearing on a symbol/directory/dependency match, so applying it
to every tier would be accidental ranking noise for those."""


def load_cache(root: Path) -> dict[str, FileIntelligenceEntry] | CacheUnavailable:
    """Load `file_intelligence.json` for `root`, or report exactly why it
    can't be used — `file_intelligence_exists()` distinguishes missing from
    corrupted, exactly like `explorer.load_cache()` does for its own four
    files. Never builds, rebuilds, or touches any other cache file."""
    if not intel_store.file_intelligence_exists(root):
        return CacheUnavailable(status="missing", message=MISSING_MESSAGE)
    entries = intel_store.load_file_intelligence(root)
    if entries is None:
        return CacheUnavailable(status="corrupted", message=CORRUPTED_MESSAGE)
    return entries


def _build_reverse_import_index(entries: dict[str, FileIntelligenceEntry]) -> dict[str, list[str]]:
    """Invert every entry's `imported_files` once: `result[target]` lists
    every file that imports `target`. The one place the reverse edge
    direction is materialized — `imported_files` persists only the outgoing
    direction on disk (QB-080's own explicit tradeoff against duplicating
    the same fact twice across the whole cache), so this in-memory
    inversion is what makes the "imported by" side of dependency matching
    possible at all. Accumulates into a `set` per target (free
    deduplication insurance against a duplicate edge), `sorted()` once per
    key at the end for a deterministic return value. `O(total import
    edges)`, computed once per `search()` call from data already fully
    loaded in memory — not a second on-disk copy, not a new traversal of
    anything `search()` wasn't already holding."""
    accumulator: dict[str, set[str]] = defaultdict(set)
    for path, entry in entries.items():
        for target in entry.imported_files:
            accumulator[target].add(path)
    return {target: sorted(sources) for target, sources in accumulator.items()}


def _dependency_neighbors(
    path: str, entry: FileIntelligenceEntry, reverse_index: dict[str, list[str]]
) -> tuple[str, ...]:
    """The deterministic, deduplicated union of `path`'s outgoing
    (`entry.imported_files`) and incoming (`reverse_index`) dependency
    edges, lexicographically sorted. A neighbor reachable via both
    directions is evaluated exactly once (a natural consequence of the
    `set` union). `_best_evidence()` calls this once per candidate file and
    never rebuilds or re-sorts the result itself."""
    return tuple(sorted({*entry.imported_files, *reverse_index.get(path, ())}))


def _path_tokens(path: str) -> frozenset[str]:
    """Every case-folded token `path` can be matched against: its filename,
    its stem, and each directory component.

        src/auth/login.py -> {"src", "auth", "login", "login.py"}

    Shared by the directory-match tier (3) and the dependency tier (7), so
    "exact component" means the same thing in both places. Safe to reuse
    for tier 3 without a separate directory-only variant: by the time tier
    3 is reached, tiers 1-2 have already ruled out a filename/stem match,
    so a hit here can only come from a directory component."""
    p = PurePosixPath(path)
    directories = (part.casefold() for part in p.parts[:-1])
    return frozenset({p.name.casefold(), p.stem.casefold(), *directories})


def _best_evidence(
    path: str,
    entry: FileIntelligenceEntry,
    reverse_index: dict[str, list[str]],
    query_casefold: str,
) -> tuple[SearchEvidence, str] | None:
    """Check all 7 evidence tiers, in priority order, first match wins — a
    file gets exactly one tier, the strongest it qualifies for. The tier
    ordering *is* this command's contract, so it stays as one legible,
    ordered checklist here rather than scattered across helpers.

    Bounded per-file cost only: this scans exactly `entry`'s own
    `top_symbols`/neighbors and the query string — it never touches any
    other file's data and never does repository-wide work, which is the
    property the whole 100ms latency budget rests on. `_dependency_neighbors()`
    is only called once all six cheaper tiers have failed to match — the
    common case (an early tier hit) never pays for building it.
    """
    p = PurePosixPath(path)
    filename = p.name
    filename_casefold = filename.casefold()
    stem_casefold = p.stem.casefold()

    for symbol in entry.top_symbols:
        if symbol.casefold() == query_casefold:
            return "exact_symbol", symbol

    if filename_casefold == query_casefold or stem_casefold == query_casefold:
        return "exact_filename", filename

    if query_casefold in _path_tokens(path):
        for part in p.parts[:-1]:
            if part.casefold() == query_casefold:
                return "exact_directory", part

    for symbol in entry.top_symbols:
        if symbol.casefold().startswith(query_casefold):
            return "prefix_symbol", symbol

    if query_casefold in filename_casefold:
        return "filename_contains", filename

    for symbol in entry.top_symbols:
        if query_casefold in symbol.casefold():
            return "top_symbol", symbol

    for neighbor in _dependency_neighbors(path, entry, reverse_index):
        if query_casefold in _path_tokens(neighbor):
            return "dependency", neighbor

    return None


def _sort_key(match: SearchMatch, query: str) -> tuple[int, int, int, int, str, int]:
    """The full, named ranking tuple — every component spelled out as a
    local so the ranking stays readable rather than an anonymous literal:
    tier, then entry-point-first, then importance, then (filename tiers
    only) closeness of filename length to the query's own length, then
    path (stable — doesn't shift as the repo evolves), then connectivity
    (higher ranks ahead of lower, the very last and most volatile
    tiebreak)."""
    tier_rank = _EVIDENCE_PRIORITY[match.evidence]
    entry_point_rank = 0 if match.entry_point else 1
    importance_rank = _IMPORTANCE_RANK[match.importance]
    if match.evidence in _FILENAME_EVIDENCE:
        filename_length_distance = abs(len(PurePosixPath(match.path).name) - len(query))
    else:
        filename_length_distance = 0
    connectivity_rank = -(match.imports + match.imported_by)
    return (
        tier_rank,
        entry_point_rank,
        importance_rank,
        filename_length_distance,
        match.path,
        connectivity_rank,
    )


def search(
    entries: dict[str, FileIntelligenceEntry],
    query: str,
    *,
    kind: FileKind | None = None,
    language: str | None = None,
    importance: FileImportance | None = None,
    entry_points_only: bool = False,
    limit: int = _DEFAULT_LIMIT,
) -> SearchResult:
    """Rank every file in `entries` against `query`, deterministically.
    `kind`/`importance` are exact (already-validated `Literal`) filters;
    `language` is a free-form, case-insensitive equality comparison against
    whatever `language` values are actually present in the cache (mixed
    ast_summarize keys and `languages.py` display names — there is no
    closed set to validate against, unlike `kind`/`importance`)."""
    query_casefold = query.casefold()
    language_casefold = language.casefold() if language is not None else None
    reverse_index = _build_reverse_import_index(entries)

    matches: list[SearchMatch] = []
    for path, entry in entries.items():
        if kind is not None and entry.kind != kind:
            continue
        if importance is not None and entry.importance != importance:
            continue
        if language_casefold is not None and entry.language.casefold() != language_casefold:
            continue
        if entry_points_only and not entry.entry_point:
            continue

        evidence = _best_evidence(path, entry, reverse_index, query_casefold)
        if evidence is None:
            continue
        tier, matched_value = evidence

        matches.append(
            SearchMatch(
                path=path,
                evidence=tier,
                matched_value=matched_value,
                language=entry.language,
                kind=entry.kind,
                importance=entry.importance,
                imports=entry.imports,
                imported_by=entry.imported_by,
                entry_point=entry.entry_point,
            )
        )

    matches.sort(key=lambda m: _sort_key(m, query))
    total_candidates = len(matches)
    limited = matches[:limit]
    return SearchResult(
        query=query,
        matches=limited,
        total_candidates=total_candidates,
        truncated=total_candidates > len(limited),
    )


def merge_search(
    entries: dict[str, FileIntelligenceEntry],
    queries: Sequence[str],
    *,
    limit: int,
    exclude: frozenset[str] = frozenset(),
) -> list[SearchMatch]:
    """Run `search()` once per query in `queries`, merge the results across
    all of them into one deduplicated list capped at `limit` — QB-081's
    only entry point for "several independent queries, one file list."

    Not a new query engine: `search()` and its private `_best_evidence()`
    still perform 100% of the per-file evidence matching, once per
    `(query, file)` pair, completely unchanged. This function only resolves
    what happens when the *same file* is matched by more than one query,
    and what deterministic order the merged list comes back in — exactly
    the two things a single `search()` call never has to decide.

    A file matched by several queries keeps only its strongest tier
    (lowest `_EVIDENCE_PRIORITY` number); ties are impossible, since
    `_best_evidence()` already returns exactly one tier for a given
    `(file, query)` pair. The merged order is `(tier, path)` — deliberately
    *not* `search()`'s own richer `_sort_key` (importance / entry-point /
    filename-length / connectivity): those extra tiebreaks are only
    meaningful relative to one query string, and a merged file may have
    been found by several different ones with no single "the" query to
    measure filename-length-closeness against. `path` alone is still a
    fully deterministic, stable secondary order, exactly as it already is
    as `_sort_key`'s own second-to-last tiebreak.

    `exclude` is checked per-match, before merging — the one caller-known
    file this must never recommend is the one already being read (see
    `claude_read.py::_maybe_prepend_relevant_files`), so there is no
    "relevant files" entry pointing back at the file whose full content
    the agent already has in hand.
    """
    best: dict[str, SearchMatch] = {}
    for query in queries:
        result = search(entries, query, limit=limit)
        for match in result.matches:
            if match.path in exclude:
                continue
            current = best.get(match.path)
            if current is None or _EVIDENCE_PRIORITY[match.evidence] < _EVIDENCE_PRIORITY[current.evidence]:
                best[match.path] = match
    ordered = sorted(best.values(), key=lambda m: (_EVIDENCE_PRIORITY[m.evidence], m.path))
    return ordered[:limit]
