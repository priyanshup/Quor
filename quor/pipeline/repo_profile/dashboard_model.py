"""`RepoDashboard` — the frozen data contract `quor repo` renders (QB-076).

Plain, frozen dataclasses, mirroring `symbols_model.py`/`graph_model.py`'s
"no Pydantic needed" convention: every field here is either copied verbatim
from an already-cached repository-intelligence artifact (`RepoIntelState`,
`RepoProfile`, cached `FileSymbols`/`FileFacts`) or a cheap, deterministic
aggregate (sum/count/group-by/sort/top-N) over one of those — never a fresh
repository walk, file read, or parse. See `dashboard.py`'s own module
docstring for exactly which field comes from where.

`LanguageShare` duplicates `model.LanguageStat`'s three fields rather than
embedding the Pydantic model directly, purely so every field in
`RepoDashboard`'s tree is uniformly `dataclasses.asdict()`-serializable —
the same "no Pydantic" reasoning `symbols_model.py`'s own docstring gives
for reusing `Symbol` as a plain dataclass instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LanguageShare:
    """One language's share of the repository — copied verbatim from
    `RepoProfile.languages` (`model.LanguageStat`), not recomputed."""

    language: str
    file_count: int
    percentage: float


@dataclass(frozen=True, slots=True)
class LargestModule:
    """One file, ranked by its own already-cached symbol count
    (`len(FileSymbols.symbols)`) — a sort over cached data, no new parsing."""

    path: str
    language: str
    symbol_count: int


@dataclass(frozen=True, slots=True)
class MostConnectedFile:
    """One file, ranked by its already-resolved dependency-graph edge
    degree (how many edges name it as source or target) — counted from
    `RepoDependencyGraph.edges`, never inferred."""

    path: str
    outgoing: int
    incoming: int
    total: int


@dataclass(frozen=True, slots=True)
class RepoDashboard:
    """The complete, deterministic repository dashboard — every field
    sourced from already-cached repository intelligence, per this module's
    own docstring."""

    root: str
    name: str
    git_head: str | None
    last_indexed: str
    """ISO-8601 UTC timestamp — `RepoIntelState.last_completed_build`,
    copied verbatim."""

    cache_age_seconds: float
    """`now - last_indexed`, in seconds — the one field derived from a live
    clock read rather than copied from cache; touches neither the
    filesystem nor the repository itself."""

    languages: list[LanguageShare] = field(default_factory=list)
    total_files: int = 0
    total_directories: int = 0
    primary_language: str | None = None

    total_symbols: int = 0
    symbols_by_language: dict[str, int] = field(default_factory=dict)

    relationship_counts: dict[str, int] = field(default_factory=dict)
    """Relationship kind -> count (e.g. "import", "inherits", "calls"),
    from the already-resolved dependency graph's edges (`Edge.kind`)."""

    graph_nodes: int = 0
    graph_edges: int = 0
    graph_resolved_edges: int = 0

    largest_modules: list[LargestModule] = field(default_factory=list)
    most_connected_files: list[MostConnectedFile] = field(default_factory=list)

    symbol_parse_failures: int = 0
    graph_parse_failures: int = 0
    profile_notes: list[str] = field(default_factory=list)
    """Passed through verbatim from `RepoProfile.notes` — already a
    deterministic, evidence-carrying fact list, never re-derived here."""
