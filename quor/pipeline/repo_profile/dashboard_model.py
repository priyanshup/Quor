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
class RepoIntelligenceStatus:
    """QB-077: how `quor repo`'s own `ensure_repo_intelligence()` call that
    just ran refreshed the cache this dashboard reads — sourced directly
    from the `RepoIntelligence` that call returned, not from any cache file
    (`RepoDashboard`'s other fields are cache-only; this one field is the
    sole exception, since it describes the refresh itself, not repository
    content)."""

    status: str
    """Human-readable outcome of this call: "Up to date" (cache hit),
    "Refreshed" (incremental), or "Rebuilt" (any full-rebuild-shaped
    action)."""

    cache_hit_ratio: float
    files_scanned: int
    files_reused: int
    rebuild_mode: str
    """"Instant" / "Incremental" / "Full" — mirrors `status` but phrased as
    a mode rather than an outcome, matching QB-077's own spec wording."""

    changed_files: int | None = None
    """Files added/modified/deleted/renamed since the last scan. `0` for a
    cache hit, `None` when this call had no prior state to diff against
    (onboarding or any forced/corrupted/version-triggered full rebuild) —
    "changed files" isn't a meaningful number in that case."""


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

    intelligence: RepoIntelligenceStatus | None = None
    """QB-077: populated by `quor repo`'s own command layer after this
    dashboard is built, from the `RepoIntelligence` its `ensure_repo_
    intelligence()` call just returned — never set by `build_dashboard()`
    itself, which stays a pure, cache-only aggregator with no knowledge of
    "what just happened." `None` for any caller that only wants the
    cache-only view `build_dashboard()` alone provides."""
