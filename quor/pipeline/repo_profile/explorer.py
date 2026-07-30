"""Repository Explorer — cache-only lookups for `quor explore` (QB-078).

**This module never walks, parses, or rebuilds anything.** It reads exactly
the four on-disk cache files `quor map`/`quor symbols`/`quor graph` already
maintain (`intel_store.py`), the same files `dashboard.py::build_dashboard()`
reads for `quor repo` — but unlike `build_dashboard()`, which deliberately
collapses "never generated" and "corrupted" into one `None` (both need the
same "run `quor map`" guidance, per its own docstring), `load_cache()` here
keeps them distinct: QB-078's own spec calls for the CLI to tell a user
"repository intelligence missing" and "cache corrupted" apart, with different
actionable guidance for each. Every other function in this module is a
lookup, filter, or small aggregate over whatever `load_cache()` returned —
no `ensure_repo_intelligence()` call anywhere in this file, no
`walk_repository()`, no source file ever read. That is QB-078's own hard
requirement (a reporting command must never trigger the multi-second cold
build `quor map`/`quor symbols`/`quor graph`/`quor repo` can), a deliberate
step back from QB-077's "auto-refresh, users shouldn't think about map/
symbols/graph" philosophy those four commands adopted — `quor explore` is
not a fifth member of that family, it is a read-only lens onto whatever they
last produced.

Dependency/reverse-dependency lookups (`file_dependencies()`/`file_used_by()`)
and the connectivity ranking behind `Importance` both filter/aggregate
`RepoDependencyGraph.edges` via `dashboard.py::connectivity_counts()`/
`dashboard.py::importance_tiers()` directly — no new graph-resolution or
traversal logic, per QB-078's own "do not duplicate graph traversal logic"
constraint. `importance_tiers()` (QB-079) is the same shared implementation
`intel.py` uses to build `file_intelligence.json`'s per-file `importance`
field, so the two never drift. Resolving *which* file an import/call/
base-class reference points to remains `graph.py`'s job alone; this module
only ever asks "what does the already-resolved graph say."
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import quor
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.dashboard import _largest_modules, importance_tiers
from quor.pipeline.repo_profile.explorer_model import (
    CacheStatus,
    CacheUnavailable,
    DependencyResult,
    FileSummary,
    Importance,
    RepoStats,
    SymbolFindResult,
    SymbolMatch,
    UsedByResult,
)
from quor.pipeline.repo_profile.graph import FileFacts, assemble_graph
from quor.pipeline.repo_profile.graph_model import RepoDependencyGraph
from quor.pipeline.repo_profile.intel_model import CACHE_SCHEMA_VERSION, RepoIntelState
from quor.pipeline.repo_profile.model import RepoProfile
from quor.pipeline.repo_profile.symbols_model import FileSymbols
from quor.pipeline.repo_profile.walk import WalkResult

MISSING_MESSAGE = (
    "No repository intelligence found for this repository. Run `quor map` "
    "(or `quor symbols` / `quor graph` / `quor repo`) once to build it — "
    "`quor explore` only reads that cache, it never builds one itself."
)
CORRUPTED_MESSAGE = (
    "The repository intelligence cache exists but could not be read. Run "
    "`quor repo --rebuild` (or `quor map --rebuild`) to rebuild it — `quor "
    "explore` only reads the cache, it never rebuilds one itself."
)


@dataclass(frozen=True, slots=True)
class ExplorerCache:
    """Everything a `quor explore` subcommand needs, loaded once from disk
    and never touched again for the rest of that CLI invocation."""

    root: Path
    state: RepoIntelState
    profile: RepoProfile
    symbol_files: dict[str, FileSymbols]
    graph_facts: dict[str, FileFacts]
    graph: RepoDependencyGraph
    cache_status: CacheStatus
    """Always `"stale"` or `"fresh"` here — `load_cache()` returns a
    `CacheUnavailable` instead for `"missing"`/`"corrupted"`, so a
    successfully-constructed `ExplorerCache` is always at least readable."""

    def is_known_path(self, path: str) -> bool:
        """Whether `path` was part of the repository as of the cache's last
        completed scan — `state.fingerprints` covers every walked file,
        including ones with zero symbols/edges (a config file, a Markdown
        doc), so this is a stricter and more useful check than "has an
        entry in `symbol_files`/`graph_facts`" alone."""
        return path in self.state.fingerprints


def load_cache(root: Path) -> ExplorerCache | CacheUnavailable:
    """Load whatever repository-intelligence cache already exists for
    `root`, or report exactly why it can't be used. Never builds, rebuilds,
    or refreshes anything — see this module's own docstring."""
    if not intel_store.state_exists(root):
        return CacheUnavailable(status="missing", message=MISSING_MESSAGE)

    state = intel_store.load_state(root)
    if state is None:
        return CacheUnavailable(status="corrupted", message=CORRUPTED_MESSAGE)

    profile = intel_store.load_profile(root)
    symbol_data = intel_store.load_symbol_facts(root)
    graph_data = intel_store.load_graph_facts(root)
    if profile is None or symbol_data is None or graph_data is None:
        return CacheUnavailable(status="corrupted", message=CORRUPTED_MESSAGE)

    symbol_files, _symbol_parse_failures = symbol_data
    graph_facts, graph_parse_failures = graph_data

    # assemble_graph()'s `walk_result` argument only feeds one advisory note
    # this module never surfaces — an empty `files` list triggers no walk of
    # any kind (mirrors `dashboard.py::build_dashboard()`'s identical use).
    placeholder_walk = WalkResult(files=[], used_git=state.git_head is not None)
    graph = assemble_graph(
        root,
        placeholder_walk,
        graph_facts,
        languages_covered=set(),
        languages_skipped=set(),
        large_file_count=0,
        parse_failure_count=len(graph_parse_failures),
    )

    quor_version = str(quor.__version__)
    cache_status: CacheStatus = "fresh"
    if state.schema_version != CACHE_SCHEMA_VERSION or state.quor_version != quor_version:
        cache_status = "stale"

    return ExplorerCache(
        root=root,
        state=state,
        profile=profile,
        symbol_files=symbol_files,
        graph_facts=graph_facts,
        graph=graph,
        cache_status=cache_status,
    )


def find_symbol(cache: ExplorerCache, name: str) -> SymbolFindResult:
    """Exact-name symbol lookup across every cached file — no fuzzy/partial
    matching, by design (QB-078's own constraint). Every symbol whose
    `Symbol.name` is byte-identical to `name` is a match; more than one is
    reported as-is (`SymbolFindResult.is_ambiguous`), never guessed at."""
    matches = [
        SymbolMatch(
            name=symbol.name,
            path=fs.path,
            language=fs.language,
            kind=symbol.kind,
            line=symbol.line,
            exports=symbol.is_public,
        )
        for fs in cache.symbol_files.values()
        for symbol in fs.symbols
        if symbol.name == name
    ]
    matches.sort(key=lambda m: (m.path, m.line))
    return SymbolFindResult(query=name, matches=matches)


def file_dependencies(cache: ExplorerCache, path: str) -> DependencyResult | None:
    """Direct outgoing dependencies of `path` — resolved `import`-kind
    edges only (an "inherits"/"calls" edge is a relationship, not what this
    command's spec means by "dependency"). Returns `None` if `path` is not
    a file this cache's last scan knew about at all (distinct from a real,
    known file with zero dependencies, which returns an empty result)."""
    if not cache.is_known_path(path):
        return None
    targets = sorted(
        {
            edge.target_file
            for edge in cache.graph.edges
            if edge.kind == "import" and edge.source_file == path and edge.target_file is not None
        }
    )
    return DependencyResult(path=path, dependencies=targets)


def file_used_by(cache: ExplorerCache, path: str) -> UsedByResult | None:
    """The exact reverse of `file_dependencies()`: every file with a
    resolved `import`-kind edge targeting `path`."""
    if not cache.is_known_path(path):
        return None
    sources = sorted(
        {
            edge.source_file
            for edge in cache.graph.edges
            if edge.kind == "import" and edge.target_file == path
        }
    )
    return UsedByResult(path=path, used_by=sources)


def file_summary(cache: ExplorerCache, path: str) -> FileSummary | None:
    """Per-file stats for `path`, or `None` if it's not a file this cache's
    last scan knew about at all."""
    if not cache.is_known_path(path):
        return None

    file_symbols = cache.symbol_files.get(path)
    file_facts = cache.graph_facts.get(path)
    language = file_symbols.language if file_symbols is not None else (file_facts.language if file_facts else "unknown")
    symbol_count = len(file_symbols.symbols) if file_symbols is not None else 0
    relationship_count = sum(1 for e in cache.graph.edges if e.source_file == path or e.target_file == path)
    import_count = sum(1 for e in cache.graph.edges if e.kind == "import" and e.source_file == path)
    export_count = sum(1 for e in cache.graph.edges if e.kind == "export" and e.source_file == path)

    return FileSummary(
        path=path,
        language=language,
        symbol_count=symbol_count,
        relationship_count=relationship_count,
        import_count=import_count,
        export_count=export_count,
        importance=_importance_tiers(cache)[path],
    )


def _importance_tiers(cache: ExplorerCache) -> dict[str, Importance]:
    """Rank every file this cache's last scan knew about by dependency-graph
    connectivity — delegates to `dashboard.importance_tiers()` (QB-079),
    the one shared implementation also used to build `file_intelligence.json`,
    rather than re-deriving the same tertile ranking a second way."""
    return importance_tiers(cache.state.fingerprints, cache.graph.edges)


def repo_stats(cache: ExplorerCache) -> RepoStats:
    """Repository-wide aggregate stats — every field either copied verbatim
    from an already-cached artifact or a cheap `Counter`/sort over one,
    mirroring `dashboard.py::build_dashboard()`'s identical provenance
    discipline."""
    total_symbols = sum(len(fs.symbols) for fs in cache.symbol_files.values())

    largest = _largest_modules(cache.symbol_files.values())
    largest_file = largest[0].path if largest else None

    import_targets: Counter[str] = Counter(
        e.target_file for e in cache.graph.edges if e.kind == "import" and e.target_file is not None
    )
    most_imported_file = min(import_targets, key=lambda p: (-import_targets[p], p)) if import_targets else None

    symbol_refs: Counter[tuple[str, str]] = Counter(
        (e.target_file, e.target_symbol)
        for e in cache.graph.edges
        if e.target_file is not None and e.target_symbol is not None
    )
    most_referenced_symbol: str | None = None
    if symbol_refs:
        (top_file, top_symbol), _count = min(symbol_refs.items(), key=lambda kv: (-kv[1], kv[0]))
        most_referenced_symbol = f"{top_symbol} ({top_file})"

    last_completed = datetime.fromisoformat(cache.state.last_completed_build)
    age_seconds = max(0.0, (datetime.now(UTC) - last_completed).total_seconds())

    return RepoStats(
        root=cache.state.repo_root,
        name=PurePosixPath(cache.state.repo_root).name or cache.state.repo_root,
        total_files=cache.profile.statistics.total_files,
        total_languages=len(cache.profile.languages),
        total_symbols=total_symbols,
        total_relationships=cache.graph.total_edges,
        dependency_edges=cache.graph.resolved_edges,
        largest_file=largest_file,
        most_imported_file=most_imported_file,
        most_referenced_symbol=most_referenced_symbol,
        intelligence_age_seconds=age_seconds,
        cache_status=cache.cache_status,
    )
