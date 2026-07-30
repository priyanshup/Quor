"""Data contracts for `quor search` — Semantic Repository Search (QB-080).

Plain, frozen dataclasses, mirroring `explorer_model.py`'s convention: every
field here is either copied verbatim from an already-cached
`FileIntelligenceEntry` or a cheap, deterministic classification/aggregate
over one — never user input, so there is no external validation boundary to
enforce. `CacheUnavailable` is reused directly from `explorer_model.py`
rather than redefined here — `quor search`'s missing/corrupted-cache shape
is identical to `quor explore`'s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from quor.pipeline.repo_profile.intel_model import FileImportance, FileKind

SearchEvidence = Literal[
    "exact_symbol",
    "exact_filename",
    "exact_directory",
    "prefix_symbol",
    "filename_contains",
    "top_symbol",
    "dependency",
]
"""The seven deterministic evidence tiers `quor search` ranks by, in
descending priority — see `search.py::_EVIDENCE_PRIORITY`/`_best_evidence()`
for the exact per-tier matching rule. `"dependency"` (QB-080) is built
entirely from `FileIntelligenceEntry.imported_files` (a resolved `import`-kind
edge, file-level only) — never a call/inherit/export edge, and never backed
by `symbol_facts.json`/`graph_facts.json` at read time."""


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """One file's search result — every field either copied verbatim from
    its `FileIntelligenceEntry` or the specific evidence that matched."""

    path: str
    evidence: SearchEvidence
    matched_value: str
    """The specific symbol name, filename, directory-path token, or related
    file path that matched the query — always populated (never a buried or
    optional field), shown prominently in text output as "Matched:"."""

    language: str
    kind: FileKind
    importance: FileImportance
    imports: int
    imported_by: int
    entry_point: bool


@dataclass(frozen=True, slots=True)
class SearchResult:
    """The complete, deterministic result of one `quor search <query>` call
    — always returned (never `None`); an empty `matches` list is a normal,
    non-error outcome (mirrors `quor explore deps`/`used-by`'s "(none)"
    convention, not `quor explore find`'s hard-fail on no match)."""

    query: str
    matches: list[SearchMatch] = field(default_factory=list)
    total_candidates: int = 0
    """How many files matched *before* `--limit` truncation."""

    truncated: bool = False
    """Whether `--limit` cut off further matches — `True` iff
    `total_candidates > len(matches)`."""
