"""Automatic Repository Intelligence (QB-072) — the orchestrator behind
`quor map`/`quor symbols`/`quor graph` no longer requiring a user to
remember they exist.

`ensure_repo_intelligence()` is the single public entry point: given a
repository root, it returns all three repository-analysis artifacts
(`RepoProfile`, `RepoSymbolIndex`, `RepoDependencyGraph`) together, doing
the least work necessary to make them correct —

- **First time seeing this repository:** print a one-time onboarding
  message, run a full scan (map + symbols + graph, never `quor schema` —
  that stays generated-on-demand, unrelated to this cache), and persist
  the result.
- **Cache missing/corrupted, or built by a different Quor version:** the
  same full scan, with a different (non-onboarding) banner explaining why.
- **Unchanged since last scan:** reuse the cached artifacts immediately —
  no repo walk beyond the cheap one needed to *confirm* nothing changed,
  no re-parsing of anything.
- **Some files changed:** re-parse only the added/modified/renamed files
  (`intel_diff.py`'s `RepoDiff`) and merge them into the cached per-file
  facts; deleted files' entries are dropped. `RepoSymbolIndex`/
  `RepoDependencyGraph` are always freshly *reassembled* from the merged
  facts (`symbols.assemble_symbol_index()`/`graph.assemble_graph()`) —
  cheap, in-memory-only aggregation/resolution, never a second cache of
  the assembled form that could drift from the facts it came from.

**Why `quor map`'s `RepoProfile` does not get the same per-file
incremental treatment.** Its fields (language percentages, detected
frameworks/build systems, entry points, ...) are whole-repository
aggregates, not a per-file-partitionable structure the way a symbol index
or a dependency graph is — there is no sound way to "recompute just the
part affected by file X" for a framework-detection heuristic that reads
several files together. Instead, `quor map`'s cached `RepoProfile` is
reused verbatim on a true cache-hit (`RepoDiff.is_empty`) and, on any
non-empty diff, `profiler.build_profile()` runs again in full — a
deliberate simplification, not an oversight, and a cheap one:
`build_profile()` never parses source code (see its own module
docstring — only manifest/config files, plus filenames/sizes), unlike
`quor symbols`/`quor graph`'s AST parsing, so re-running it whenever
anything changed is inexpensive relative to what incremental treatment
would save.

No watchers, no daemon, no background service, no polling — every check
this module performs happens synchronously, once, inside the CLI command
invocation that called it (`quor map`/`quor symbols`/`quor graph`), never
on Quor's hook dispatch path (see `docs/design/QB-072-automatic-repo-
intelligence.md` for why that boundary was chosen).
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import quor
from quor.pipeline.ast_summarize.registry import (
    EXTENSION_TO_LANGUAGE,
    get_relationship_extractor,
    get_symbol_extractor,
    is_language_available,
)
from quor.pipeline.repo_profile import intel_store
from quor.pipeline.repo_profile.graph import (
    _MAX_FILE_SIZE_BYTES as _GRAPH_MAX_FILE_SIZE_BYTES,
)
from quor.pipeline.repo_profile.graph import (
    FileFacts,
    assemble_graph,
    build_dependency_graph_with_facts,
    extract_file_facts,
)
from quor.pipeline.repo_profile.graph_model import RepoDependencyGraph
from quor.pipeline.repo_profile.intel_diff import diff_repository, fingerprint_files, git_head
from quor.pipeline.repo_profile.intel_model import (
    CACHE_SCHEMA_VERSION,
    BuildAction,
    RepoDiff,
    RepoIntelligence,
    RepoIntelState,
)
from quor.pipeline.repo_profile.profiler import build_profile
from quor.pipeline.repo_profile.symbols import (
    _MAX_FILE_SIZE_BYTES as _SYMBOLS_MAX_FILE_SIZE_BYTES,
)
from quor.pipeline.repo_profile.symbols import (
    assemble_symbol_index,
    build_symbol_index_with_facts,
    extract_file_symbols,
)
from quor.pipeline.repo_profile.symbols_model import FileSymbols, RepoSymbolIndex
from quor.pipeline.repo_profile.walk import WalkResult, walk_repository

Echo = Callable[[str], None]

_ONBOARDING_BANNER = (
    "[quor] New repository detected. Preparing repository intelligence "
    "(map, symbols, dependency graph) — this happens once; future runs "
    "reuse the cache and rebuild only what changed."
)
_CORRUPTED_BANNER = (
    "[quor] Repository intelligence cache was unreadable — rebuilding from scratch."
)
_VERSION_BANNER = (
    "[quor] Quor version changed since this cache was built — rebuilding repository intelligence."
)

_ACTION_BANNERS: dict[BuildAction, str | None] = {
    "onboarded": _ONBOARDING_BANNER,
    "corrupted_rebuild": _CORRUPTED_BANNER,
    "version_rebuild": _VERSION_BANNER,
    "forced_rebuild": None,
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _noop(_message: str) -> None:
    pass


def ensure_repo_intelligence(root: Path, *, rebuild: bool = False, echo: Echo | None = None) -> RepoIntelligence:
    """Return `root`'s repository intelligence, building or refreshing the
    cache exactly as much as needed. `echo` receives progress/onboarding
    messages (pass `typer.echo` with `err=True` from a CLI command so they
    never land on stdout the caller might be piping as `--json`); omitted,
    messages are silently discarded (useful for tests and non-interactive
    callers)."""
    t0 = time.monotonic()
    _echo = echo or _noop
    quor_version = str(quor.__version__)

    if rebuild:
        return _full_rebuild(root, action="forced_rebuild", t0=t0, echo=_echo, quor_version=quor_version)

    if not intel_store.state_exists(root):
        return _full_rebuild(root, action="onboarded", t0=t0, echo=_echo, quor_version=quor_version)

    state = intel_store.load_state(root)
    if state is None:
        return _full_rebuild(root, action="corrupted_rebuild", t0=t0, echo=_echo, quor_version=quor_version)

    if state.schema_version != CACHE_SCHEMA_VERSION or state.quor_version != quor_version:
        return _full_rebuild(root, action="version_rebuild", t0=t0, echo=_echo, quor_version=quor_version)

    return _refresh_from_cache(root, state, t0=t0, echo=_echo, quor_version=quor_version)


def _full_rebuild(root: Path, *, action: BuildAction, t0: float, echo: Echo, quor_version: str) -> RepoIntelligence:
    banner = _ACTION_BANNERS.get(action)
    if banner:
        echo(banner)

    echo("Scanning repository...")
    walk_result = walk_repository(root)

    echo("Building repository intelligence...")
    profile = build_profile(root, walk_result=walk_result)

    echo("Building symbols...")
    symbol_index, symbol_files, symbol_parse_failures = build_symbol_index_with_facts(
        root, walk_result=walk_result
    )

    echo("Building dependency graph...")
    graph, graph_facts, graph_parse_failures = build_dependency_graph_with_facts(root, walk_result=walk_result)

    fingerprints = fingerprint_files(root, walk_result.files)
    now = _utc_now_iso()
    intel_store.save_state(
        root,
        RepoIntelState(
            schema_version=CACHE_SCHEMA_VERSION,
            quor_version=quor_version,
            repo_root=root.resolve().as_posix(),
            git_head=git_head(root),
            file_count=len(walk_result.files),
            last_scan_timestamp=now,
            last_completed_build=now,
            fingerprints=fingerprints,
        ),
    )
    intel_store.save_profile(root, profile)
    intel_store.save_symbol_facts(root, symbol_files, symbol_parse_failures)
    intel_store.save_graph_facts(root, graph_facts, graph_parse_failures)

    elapsed = time.monotonic() - t0
    echo(f"Finished in {elapsed:.1f} seconds.")
    return RepoIntelligence(
        profile=profile,
        symbols=symbol_index,
        graph=graph,
        action=action,
        elapsed_seconds=elapsed,
        diff=None,
        files_scanned=len(walk_result.files),
        files_reextracted=len(walk_result.files),
    )


def _refresh_from_cache(
    root: Path, state: RepoIntelState, *, t0: float, echo: Echo, quor_version: str
) -> RepoIntelligence:
    profile = intel_store.load_profile(root)
    symbol_data = intel_store.load_symbol_facts(root)
    graph_data = intel_store.load_graph_facts(root)
    if profile is None or symbol_data is None or graph_data is None:
        # state.json was fine but a sibling artifact file is missing or
        # corrupted — treat the whole cache as unusable rather than
        # returning a partially-stale RepoIntelligence.
        return _full_rebuild(root, action="corrupted_rebuild", t0=t0, echo=echo, quor_version=quor_version)
    symbol_files, symbol_parse_failures = symbol_data
    graph_facts, graph_parse_failures = graph_data

    walk_result = walk_repository(root)
    diff, new_fingerprints = diff_repository(root, walk_result.files, state.fingerprints)
    now = _utc_now_iso()

    if diff.is_empty:
        # Confirmed unchanged: still worth recording that a scan happened
        # just now (the state file's own `last_scan_timestamp` promise),
        # but nothing was rebuilt, so `last_completed_build` does not
        # advance and no artifact file is rewritten.
        intel_store.save_state(
            root,
            RepoIntelState(
                schema_version=CACHE_SCHEMA_VERSION,
                quor_version=quor_version,
                repo_root=root.resolve().as_posix(),
                git_head=git_head(root),
                file_count=len(walk_result.files),
                last_scan_timestamp=now,
                last_completed_build=state.last_completed_build,
                fingerprints=new_fingerprints,
            ),
        )
    else:
        echo("Scanning repository...")
        echo("Building repository intelligence...")
        profile = build_profile(root, walk_result=walk_result)

        echo("Building symbols...")
        symbol_files, symbol_parse_failures = _refresh_symbol_facts(root, diff, symbol_files, symbol_parse_failures)

        echo("Building dependency graph...")
        graph_facts, graph_parse_failures = _refresh_graph_facts(root, diff, graph_facts, graph_parse_failures)

        intel_store.save_state(
            root,
            RepoIntelState(
                schema_version=CACHE_SCHEMA_VERSION,
                quor_version=quor_version,
                repo_root=root.resolve().as_posix(),
                git_head=git_head(root),
                file_count=len(walk_result.files),
                last_scan_timestamp=now,
                last_completed_build=now,
                fingerprints=new_fingerprints,
            ),
        )
        intel_store.save_profile(root, profile)
        intel_store.save_symbol_facts(root, symbol_files, symbol_parse_failures)
        intel_store.save_graph_facts(root, graph_facts, graph_parse_failures)

    symbol_index = _assemble_symbols(root, walk_result, symbol_files, symbol_parse_failures)
    graph = _assemble_graph(root, walk_result, graph_facts, graph_parse_failures)

    elapsed = time.monotonic() - t0
    action: BuildAction = "cache_hit" if diff.is_empty else "incremental"
    if not diff.is_empty:
        echo(f"Finished in {elapsed:.1f} seconds.")
    return RepoIntelligence(
        profile=profile,
        symbols=symbol_index,
        graph=graph,
        action=action,
        elapsed_seconds=elapsed,
        diff=diff,
        files_scanned=len(walk_result.files),
        files_reextracted=0 if diff.is_empty else len(diff.reextraction_paths),
    )


def _refresh_symbol_facts(
    root: Path, diff: RepoDiff, cached_files: dict[str, FileSymbols], cached_parse_failures: set[str]
) -> tuple[dict[str, FileSymbols], set[str]]:
    files = dict(cached_files)
    parse_failures = set(cached_parse_failures)

    for old_path in diff.deleted:
        files.pop(old_path, None)
        parse_failures.discard(old_path)

    for old_path, new_path in diff.renamed:
        # Pure rename: content is byte-identical to what the old path
        # already had cached — relocate, never re-parse (see
        # RepoDiff.reextraction_paths's docstring for the bug this fixed).
        moved = files.pop(old_path, None)
        was_failure = old_path in parse_failures
        parse_failures.discard(old_path)
        if moved is not None:
            files[new_path] = dataclasses.replace(moved, path=new_path)
        elif was_failure:
            parse_failures.add(new_path)
        # else: old_path had zero symbols and wasn't a failure either —
        # new_path correctly inherits that same "nothing to record" outcome.

    for rel_path in diff.reextraction_paths:
        files.pop(rel_path, None)
        parse_failures.discard(rel_path)
        language = EXTENSION_TO_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower())
        if language is None or not is_language_available(language) or get_symbol_extractor(language) is None:
            continue
        file_symbols, reason = extract_file_symbols(root, rel_path, language)
        if reason == "parse_failure":
            parse_failures.add(rel_path)
        elif file_symbols is not None:
            files[rel_path] = file_symbols
    return files, parse_failures


def _refresh_graph_facts(
    root: Path, diff: RepoDiff, cached_facts: dict[str, FileFacts], cached_parse_failures: set[str]
) -> tuple[dict[str, FileFacts], set[str]]:
    facts = dict(cached_facts)
    parse_failures = set(cached_parse_failures)

    for old_path in diff.deleted:
        facts.pop(old_path, None)
        parse_failures.discard(old_path)

    for old_path, new_path in diff.renamed:
        # Pure rename — relocate the cached facts, never re-parse (FileFacts
        # carries no `path` field of its own, unlike FileSymbols, so this is
        # a plain key move with no field to update).
        moved = facts.pop(old_path, None)
        was_failure = old_path in parse_failures
        parse_failures.discard(old_path)
        if moved is not None:
            facts[new_path] = moved
        elif was_failure:
            parse_failures.add(new_path)

    for rel_path in diff.reextraction_paths:
        facts.pop(rel_path, None)
        parse_failures.discard(rel_path)
        language = EXTENSION_TO_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower())
        if (
            language is None
            or not is_language_available(language)
            or get_symbol_extractor(language) is None
            or get_relationship_extractor(language) is None
        ):
            continue
        file_facts, reason = extract_file_facts(root, rel_path, language)
        if reason == "parse_failure":
            parse_failures.add(rel_path)
        elif file_facts is not None:
            facts[rel_path] = file_facts
    return facts, parse_failures


def _current_language_coverage(files: list[str]) -> tuple[set[str], set[str]]:
    """Fresh, cheap (no file-content reads) per-build recomputation of
    which languages the current file list maps to and whether each one's
    optional dependency is currently installed — deliberately *not*
    persisted/diffed, since it depends only on the current walk and the
    current Python environment, both trivial to re-derive every call (see
    module docstring)."""
    covered: set[str] = set()
    skipped: set[str] = set()
    for rel_path in files:
        language = EXTENSION_TO_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower())
        if language is None:
            continue
        if is_language_available(language):
            covered.add(language)
        else:
            skipped.add(language)
    return covered, skipped


def _assemble_symbols(
    root: Path, walk_result: WalkResult, files: dict[str, FileSymbols], parse_failures: set[str]
) -> RepoSymbolIndex:
    walk_set = set(walk_result.files)
    covered, skipped = _current_language_coverage(walk_result.files)
    large_file_count = sum(
        1
        for rel_path in walk_result.files
        if rel_path not in files
        and rel_path not in parse_failures
        and EXTENSION_TO_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower()) in covered
        and _file_size(root, rel_path) > _SYMBOLS_MAX_FILE_SIZE_BYTES
    )
    # Defensive against the four cache files ever being read back from
    # slightly different points in time (each is written atomically on its
    # own, but not all four as one cross-file transaction) — never let a
    # stale entry for a file no longer in the current walk leak through.
    relevant_failures = parse_failures & walk_set
    relevant_files = {p: f for p, f in files.items() if p in walk_set}
    return assemble_symbol_index(
        root,
        walk_result,
        list(relevant_files.values()),
        languages_covered=covered,
        languages_skipped=skipped,
        large_file_count=large_file_count,
        parse_failure_count=len(relevant_failures),
    )


def _assemble_graph(
    root: Path, walk_result: WalkResult, facts: dict[str, FileFacts], parse_failures: set[str]
) -> RepoDependencyGraph:
    walk_set = set(walk_result.files)
    covered, skipped = _current_language_coverage(walk_result.files)
    large_file_count = sum(
        1
        for rel_path in walk_result.files
        if rel_path not in facts
        and rel_path not in parse_failures
        and EXTENSION_TO_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower()) in covered
        and _file_size(root, rel_path) > _GRAPH_MAX_FILE_SIZE_BYTES
    )
    relevant_failures = parse_failures & walk_set
    relevant_facts = {p: f for p, f in facts.items() if p in walk_set}
    return assemble_graph(
        root,
        walk_result,
        relevant_facts,
        languages_covered=covered,
        languages_skipped=skipped,
        large_file_count=large_file_count,
        parse_failure_count=len(relevant_failures),
    )


def _file_size(root: Path, rel_path: str) -> int:
    try:
        return (root / rel_path).stat().st_size
    except OSError:
        return 0
