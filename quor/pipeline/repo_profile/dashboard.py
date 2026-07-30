"""Repository Dashboard aggregation (QB-076, `quor repo`).

`build_dashboard()` is the single public entry point: given a repository
root, it reads whatever repository intelligence is already cached on disk
(`quor/pipeline/repo_profile/intel_store.py` — the same four JSON files
`quor map`/`quor symbols`/`quor graph` maintain) and aggregates it into one
compact `RepoDashboard`. It **never** builds, rebuilds, or refreshes that
cache: no `walk_repository()`, no `ensure_repo_intelligence()`, no source
file is read or parsed. If no usable cache exists (never generated, or
corrupted/unreadable), it returns `None` and the CLI command explains what
to run — see `quor/cli/commands/repo.py`.

Every field on the returned `RepoDashboard` is either a verbatim copy of an
already-computed field or a cheap, deterministic aggregate (sum, count,
group-by, sort, top-N) over already-cached per-file facts — see
`dashboard_model.py`'s own docstring for field-by-field provenance. The one
partial exception is `RepoDependencyGraph` edges: which raw import/call/
inherits target resolves to which file is genuine resolution logic, not a
trivial aggregate. Rather than re-implementing that a second time, this
module calls the exact same, already-tested `graph.assemble_graph()` that
`quor graph` itself uses, fed directly from the cached `graph_facts.json`
(`intel_store.load_graph_facts()`). Reading `assemble_graph()`'s own source
confirms its `walk_result` argument is used for exactly one thing — a
`used_git` advisory note this module doesn't surface — so a placeholder
`WalkResult` with an empty `files` list triggers no repository walk of any
kind. This module builds its own, separately-sourced Repository Health
section instead of reusing that call's `notes`/`languages_covered`/
`languages_skipped` output, so those are passed as inert values and
discarded.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from quor.pipeline.ast_summarize.symbol_model import Symbol
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.dashboard_model import (
    LanguageShare,
    LargestModule,
    MostConnectedFile,
    RepoDashboard,
)
from quor.pipeline.repo_profile.graph import assemble_graph
from quor.pipeline.repo_profile.graph_model import Edge
from quor.pipeline.repo_profile.symbols_model import FileSymbols
from quor.pipeline.repo_profile.walk import WalkResult

_TOP_N = 10


def build_dashboard(root: Path) -> RepoDashboard | None:
    """Return `root`'s dashboard from cached repository intelligence, or
    `None` if no usable cache exists. Never generated and corrupted both
    map to `None` — the caller can't distinguish the two from the return
    value alone and doesn't need to: both need the same "run `quor map`"
    guidance."""
    state = intel_store.load_state(root)
    profile = intel_store.load_profile(root)
    symbol_data = intel_store.load_symbol_facts(root)
    graph_data = intel_store.load_graph_facts(root)
    if state is None or profile is None or symbol_data is None or graph_data is None:
        return None

    symbol_files, symbol_parse_failures = symbol_data
    graph_facts, graph_parse_failures = graph_data

    # assemble_graph()'s `walk_result` argument only feeds one advisory note
    # this module never surfaces (see module docstring) — an empty `files`
    # list costs nothing and triggers no walk; `used_git` is filled in
    # honestly anyway in case a future change to assemble_graph starts
    # reading more of it.
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

    now = datetime.now(UTC)
    last_indexed = datetime.fromisoformat(state.last_completed_build)
    cache_age_seconds = max(0.0, (now - last_indexed).total_seconds())

    return RepoDashboard(
        root=state.repo_root,
        name=PurePosixPath(state.repo_root).name or state.repo_root,
        git_head=state.git_head,
        last_indexed=state.last_completed_build,
        cache_age_seconds=cache_age_seconds,
        languages=[
            LanguageShare(language=lang.language, file_count=lang.file_count, percentage=lang.percentage)
            for lang in profile.languages
        ],
        total_files=profile.statistics.total_files,
        total_directories=profile.statistics.total_directories,
        primary_language=profile.statistics.primary_language,
        total_symbols=sum(len(fs.symbols) for fs in symbol_files.values()),
        symbols_by_language=_symbols_by_language(symbol_files.values()),
        relationship_counts=dict(Counter(e.kind for e in graph.edges)),
        graph_nodes=_graph_node_count(graph.edges),
        graph_edges=graph.total_edges,
        graph_resolved_edges=graph.resolved_edges,
        largest_modules=_largest_modules(symbol_files.values()),
        most_connected_files=_most_connected_files(graph.edges),
        symbol_parse_failures=len(symbol_parse_failures),
        graph_parse_failures=len(graph_parse_failures),
        profile_notes=list(profile.notes),
    )


def _symbols_by_language(files: Iterable[FileSymbols]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for fs in files:
        counts[fs.language] += len(fs.symbols)
    return dict(sorted(counts.items()))


def _largest_modules(files: Iterable[FileSymbols]) -> list[LargestModule]:
    def _symbol_count(fs: FileSymbols) -> int:
        symbols: list[Symbol] = fs.symbols
        return len(symbols)

    ranked = sorted(files, key=lambda fs: (-_symbol_count(fs), fs.path))
    return [
        LargestModule(path=fs.path, language=fs.language, symbol_count=_symbol_count(fs))
        for fs in ranked[:_TOP_N]
        if fs.symbols
    ]


def _graph_node_count(edges: Iterable[Edge]) -> int:
    nodes: set[str] = set()
    for e in edges:
        nodes.add(e.source_file)
        if e.target_file is not None:
            nodes.add(e.target_file)
    return len(nodes)


def connectivity_counts(edges: Iterable[Edge]) -> tuple[Counter[str], Counter[str]]:
    """`(outgoing, incoming)` edge-degree counters, keyed by file path —
    every edge's `source_file` increments `outgoing`, every non-`None`
    `target_file` increments `incoming`. Factored out of
    `_most_connected_files()` (QB-078) so `quor explore`'s per-file
    "Repository importance" tiering can rank *every* file's connectivity,
    not just this module's own top-`_TOP_N` dashboard listing, without a
    second implementation of the same `Counter` walk over `edges`."""
    outgoing: Counter[str] = Counter()
    incoming: Counter[str] = Counter()
    for e in edges:
        outgoing[e.source_file] += 1
        if e.target_file is not None:
            incoming[e.target_file] += 1
    return outgoing, incoming


def _most_connected_files(edges: Iterable[Edge]) -> list[MostConnectedFile]:
    outgoing, incoming = connectivity_counts(edges)
    files = set(outgoing) | set(incoming)
    ranked = sorted(files, key=lambda p: (-(outgoing[p] + incoming[p]), p))
    return [
        MostConnectedFile(path=p, outgoing=outgoing[p], incoming=incoming[p], total=outgoing[p] + incoming[p])
        for p in ranked[:_TOP_N]
    ]
