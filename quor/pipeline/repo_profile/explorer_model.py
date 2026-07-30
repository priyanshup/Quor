"""Data contracts for `quor explore` — Repository Explorer (QB-078).

Plain, frozen dataclasses throughout, mirroring `dashboard_model.py`'s "no
Pydantic needed" reasoning: every field here is either copied verbatim from
an already-cached repository-intelligence artifact or a cheap, deterministic
aggregate/filter over one (see `explorer.py`'s own module docstring for the
"cache-only, never walks/parses/rebuilds" contract these types serve) —
never user input, so there is no external validation boundary to enforce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CacheStatus = Literal["missing", "corrupted", "stale", "fresh"]
"""Four states a `quor explore` subcommand's underlying cache can be in —
see `explorer.py::load_cache()`'s own docstring for exactly how each is
detected:
- `missing` — no repository-intelligence cache exists yet for this
  repository (no `state.json` at all).
- `corrupted` — a cache exists but could not be read back (unreadable
  `state.json`, or a sibling artifact file missing/unreadable).
- `stale` — the cache reads back fine, but was built by a different Quor
  version or cache schema than this one (the same condition
  `intel.py::ensure_repo_intelligence()` treats as `"version_rebuild"`) —
  usable, but possibly out of date; `quor explore` reports it, never
  rebuilds it.
- `fresh` — readable and version/schema-matched. Says nothing about
  whether repository *content* has changed since the cache was built
  (checking that would require a repository walk, which this command must
  never do) — `intelligence_age_seconds` (see `RepoStats`) is the only
  signal offered for that.
"""

Importance = Literal["High", "Medium", "Low"]
"""A file's dependency-graph connectivity rank among every file this
repository's cache tracked, tertile-split (top third `High`, middle third
`Medium`, bottom third `Low`) — see `explorer.py::_importance_tiers()`."""


@dataclass(frozen=True, slots=True)
class CacheUnavailable:
    """`load_cache()`'s failure case: `status` is always `"missing"` or
    `"corrupted"` (never `"stale"`/`"fresh"`, which are usable — see
    `ExplorerCache.cache_status`) — `message` is the actionable, user-facing
    guidance for that specific state."""

    status: Literal["missing", "corrupted"]
    message: str


@dataclass(frozen=True, slots=True)
class SymbolMatch:
    """One symbol declaration matching a `quor explore find` query."""

    name: str
    path: str
    language: str
    kind: str
    line: int
    exports: bool
    """Reuses `Symbol.is_public` verbatim under the label this command's
    spec uses — see `Symbol.is_public`'s own docstring: for JS/TS it
    directly *is* the module-export mechanism, and for every other
    supported language it's that language's own closest deterministic
    analogue (Python/Go leading-case-or-underscore convention, explicit
    `public`/`pub` modifiers for Java/Rust/C#) — not a new, separately
    computed notion of "exported"."""


@dataclass(frozen=True, slots=True)
class SymbolFindResult:
    """The complete result of one `quor explore find <name>` query —
    always returned (never `None`); an empty `matches` list is the "not
    found" case, which the CLI layer renders as an error."""

    query: str
    matches: list[SymbolMatch] = field(default_factory=list)

    @property
    def is_ambiguous(self) -> bool:
        return len(self.matches) > 1


@dataclass(frozen=True, slots=True)
class DependencyResult:
    """`quor explore deps <file>`'s result — direct, resolved `import`-kind
    outgoing edges only (see `explorer.py::file_dependencies()`'s own
    docstring for why other relationship kinds are excluded)."""

    path: str
    dependencies: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.dependencies)


@dataclass(frozen=True, slots=True)
class UsedByResult:
    """`quor explore used-by <file>`'s result — the exact reverse of
    `DependencyResult`: every file with a resolved `import`-kind edge
    targeting this one."""

    path: str
    used_by: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.used_by)


@dataclass(frozen=True, slots=True)
class FileSummary:
    """`quor explore file <path>`'s result."""

    path: str
    language: str
    symbol_count: int
    relationship_count: int
    """Every edge (any kind) with this file as source or target — a
    superset of `import_count`/`export_count`, which are `kind`-filtered,
    source-only subsets of it."""

    import_count: int
    export_count: int
    importance: Importance


@dataclass(frozen=True, slots=True)
class RepoStats:
    """`quor explore stats`'s result."""

    root: str
    name: str
    total_files: int
    total_languages: int
    total_symbols: int
    total_relationships: int
    """`RepoDependencyGraph.total_edges` verbatim — every extracted
    relationship fact, resolved or not."""

    dependency_edges: int
    """`RepoDependencyGraph.resolved_edges` verbatim — edges with a
    non-`None` `target_file`, i.e. relationships actually pointing at a
    specific other file in this repository."""

    largest_file: str | None
    most_imported_file: str | None
    most_referenced_symbol: str | None
    """`"<symbol> (<file>)"`, or `None` if no edge in this repository
    resolves to a specific target symbol."""

    intelligence_age_seconds: float
    cache_status: CacheStatus
