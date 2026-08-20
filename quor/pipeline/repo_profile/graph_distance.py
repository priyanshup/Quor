"""Graph-distance (hop) tiering for MCP repository-context payloads —
metadata-enrichment work giving an agent a cheap answer to "how far is this
file from the one I'm actually looking at": 0-hop (the focus file itself),
1-hop (a direct import/importer), 2-hop (reachable via one intermediate),
and so on up to a caller-chosen cap.

Genuinely new: nothing in this codebase computed graph distance beyond a
single hop before this. Built entirely on top of `file_intelligence.json`'s
already-cached `FileIntelligenceEntry.imported_files` (QB-080) plus
`search.py`'s own in-memory reverse-import index
(`_build_reverse_import_index()`, reused directly here rather than
reimplemented — the one place the "imported by" edge direction is
materialized, per that function's own docstring) — extended from
`search.py`'s strictly-1-hop `_dependency_neighbors()` union into a
caller-bounded breadth-first search. No second cache file, no new
on-disk data: everything here is an in-memory traversal of data
`get_repo_context` already has loaded.
"""

from __future__ import annotations

from collections import deque

from quor.pipeline.repo_profile.intel_model import FileIntelligenceEntry
from quor.pipeline.repo_profile.search import _build_reverse_import_index

DEFAULT_MAX_HOPS = 2
"""R-08-style tiering cap: 0-hop focus target, 1-hop direct import/importer,
2-hop outline (reachable via exactly one intermediate file). Anything
farther is deliberately left unannotated rather than computed — a caller
that needs a wider radius passes a larger `max_hops` explicitly."""


def compute_hop_distances(
    entries: dict[str, FileIntelligenceEntry],
    start: str,
    *,
    max_hops: int = DEFAULT_MAX_HOPS,
) -> dict[str, int]:
    """Breadth-first search from `start` over the union of outgoing
    (`imported_files`) and incoming (reverse-import) dependency edges,
    capped at `max_hops`. Returns `{path: hop_distance}` for every file
    reached within `max_hops`, including `start` itself at distance 0.

    Deterministic and bounded: each file is visited exactly once (the
    `distances` dict doubles as the visited-set), so this is `O(files
    reached within max_hops)`, never `O(repo size)` — the same latency
    discipline `search.py`'s own module docstring requires of anything
    built from `file_intelligence.json`. `start` need not be a key in
    `entries` (nothing else in this codebase requires the focus file to
    already have a cached entry) — if it's absent, only `{start: 0}` is
    returned, since there's no known neighbor to expand from.
    """
    reverse_index = _build_reverse_import_index(entries)
    distances: dict[str, int] = {start: 0}
    frontier: deque[str] = deque([start])

    while frontier:
        current = frontier.popleft()
        current_distance = distances[current]
        if current_distance >= max_hops:
            continue

        entry = entries.get(current)
        outgoing = entry.imported_files if entry is not None else ()
        incoming = reverse_index.get(current, ())

        for neighbor in {*outgoing, *incoming}:
            if neighbor in distances:
                continue
            distances[neighbor] = current_distance + 1
            frontier.append(neighbor)

    return distances
