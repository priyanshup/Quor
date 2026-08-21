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
import warnings
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import regex

import quor
from quor.pipeline.ast_summarize.registry import (
    EXTENSION_TO_LANGUAGE,
    get_relationship_extractor,
    get_symbol_extractor,
    is_language_available,
)
from quor.pipeline.repo_profile import dashboard, intel_store
from quor.pipeline.repo_profile._longpath import to_long_path
from quor.pipeline.repo_profile.graph import (
    _MAX_FILE_SIZE_BYTES as _GRAPH_MAX_FILE_SIZE_BYTES,
)
from quor.pipeline.repo_profile.graph import (
    FileFacts,
    assemble_graph,
)
from quor.pipeline.repo_profile.graph_model import Edge, RepoDependencyGraph
from quor.pipeline.repo_profile.intel_diff import diff_repository, fingerprint_files, git_head
from quor.pipeline.repo_profile.intel_model import (
    CACHE_SCHEMA_VERSION,
    BuildAction,
    FileFingerprint,
    FileIntelligenceEntry,
    FileKind,
    RepoDiff,
    RepoIntelligence,
    RepoIntelState,
)
from quor.pipeline.repo_profile.languages import is_vendor_or_build_path, language_for_path
from quor.pipeline.repo_profile.model import RepoProfile
from quor.pipeline.repo_profile.profiler import build_profile
from quor.pipeline.repo_profile.symbols import (
    _MAX_FILE_SIZE_BYTES as _SYMBOLS_MAX_FILE_SIZE_BYTES,
)
from quor.pipeline.repo_profile.symbols import (
    SkipReason,
    assemble_symbol_index,
)
from quor.pipeline.repo_profile.symbols_model import FileSymbols, RepoSymbolIndex
from quor.pipeline.repo_profile.walk import WalkResult, walk_repository

Echo = Callable[[str], None]

_ONBOARDING_BANNER = (
    "[quor] New repository detected. Preparing repository intelligence "
    "(map, symbols, dependency graph) — this happens once; future runs "
    "reuse the cache and rebuild only what changed."
)
_CORRUPTED_BANNER = "[quor] Repository intelligence cache was unreadable — rebuilding from scratch."
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


def ensure_repo_intelligence(
    root: Path, *, rebuild: bool = False, echo: Echo | None = None
) -> RepoIntelligence:
    """Return `root`'s repository intelligence, building or refreshing the
    cache exactly as much as needed. `echo` receives progress/onboarding
    messages (pass `typer.echo` with `err=True` from a CLI command so they
    never land on stdout the caller might be piping as `--json`); omitted,
    messages are silently discarded (useful for tests and non-interactive
    callers)."""
    _cleanup_repo_intel_safe()

    t0 = time.monotonic()
    _echo = echo or _noop
    quor_version = str(quor.__version__)

    if rebuild:
        return _full_rebuild(
            root, action="forced_rebuild", t0=t0, echo=_echo, quor_version=quor_version
        )

    if not intel_store.state_exists(root):
        return _full_rebuild(root, action="onboarded", t0=t0, echo=_echo, quor_version=quor_version)

    state = intel_store.load_state(root)
    if state is None:
        return _full_rebuild(
            root, action="corrupted_rebuild", t0=t0, echo=_echo, quor_version=quor_version
        )

    if state.schema_version != CACHE_SCHEMA_VERSION or state.quor_version != quor_version:
        return _full_rebuild(
            root, action="version_rebuild", t0=t0, echo=_echo, quor_version=quor_version
        )

    return _refresh_from_cache(root, state, t0=t0, echo=_echo, quor_version=quor_version)


def _cleanup_repo_intel_safe() -> None:
    """Run repo_intel retention (QB-124), fail-open — a cleanup error must
    never block `quor map`/`symbols`/`graph`/`repo`/`search`/`explore` from
    doing the real work the user asked for. Mirrors
    `quor/engine/dispatcher.py`'s `_cleanup_tee_safe()`: loads user config
    for the configured ceilings, `cleanup_repo_intel()` throttles itself
    internally so this is cheap to call on every invocation."""
    try:
        from quor.config.loader import load_user_config
        from quor.pipeline.repo_profile.intel_cleanup import cleanup_repo_intel

        user_config = load_user_config()
        cleanup_repo_intel(
            max_age_days=user_config.repo_intel_max_age_days,
            max_bytes=user_config.repo_intel_max_bytes,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] repo_intel cleanup error: {exc}", stacklevel=1)


# ---------------------------------------------------------------------------
# file_intelligence.json (QB-079) — build-time computation.
#
# A general-purpose per-file cache, not a Read-hook-specific artifact (see
# intel_model.py::FileIntelligenceEntry's own docstring); computed once per
# build/refresh pass here, alongside the existing save_state()/save_profile()/
# save_symbol_facts()/save_graph_facts() calls, and read back in O(1) by
# consumers (claude_read.py first, quor explore/quor repo potentially later)
# that must never call ensure_repo_intelligence()/walk_repository() themselves.
# ---------------------------------------------------------------------------

_PRIMARY_SYMBOL_KINDS = frozenset({"class", "interface", "struct", "trait", "enum", "function"})
_MAX_TOP_SYMBOLS = 5

_TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__", "spec", "specs"})

_GENERATED_BASENAME_SUFFIXES = (
    "_pb2.py",
    "_pb2_grpc.py",
    ".pb.go",
    ".g.cs",
    ".g.dart",
    ".min.js",
    ".min.css",
)
_GENERATED_NAME_SUBSTRINGS = ("_generated.", ".generated.")
_GENERATED_CONTENT_RE = r"(@generated\b|DO NOT EDIT|Code generated .* DO NOT EDIT)"
_GENERATED_SCAN_TIMEOUT = 1.0
_MAX_SCAN_BYTES = 65_536
"""Matches entry_points.py's own `_MAX_SCAN_BYTES` bound — a generated-file
marker, like a `__main__` guard, is always near the top of the file."""


def _primary_symbol_names(file_symbols: FileSymbols | None) -> list[str]:
    """Public, top-level declaration names only (methods excluded — a
    method isn't a standalone "thing this file defines" the way a
    module-level class/function is), declaration-line order, de-duplicated,
    capped at `_MAX_TOP_SYMBOLS`. `[]` for a file with no `FileSymbols` at
    all (not AST-covered, or failed to parse)."""
    if file_symbols is None:
        return []
    candidates = sorted(
        (s for s in file_symbols.symbols if s.is_public and s.kind in _PRIMARY_SYMBOL_KINDS),
        key=lambda s: s.line,
    )
    names: list[str] = []
    seen: set[str] = set()
    for s in candidates:
        if s.name in seen:
            continue
        seen.add(s.name)
        names.append(s.name)
        if len(names) >= _MAX_TOP_SYMBOLS:
            break
    return names


def _is_test_path(rel_path: str) -> bool:
    """Naming-convention evidence only — the same "convention as proof"
    rigor `entry_points.py`'s `__main__`-guard detection and
    `Symbol.is_public`'s per-language visibility rules already use."""
    posix = PurePosixPath(rel_path)
    if any(part.lower() in _TEST_DIR_NAMES for part in posix.parts[:-1]):
        return True
    name = posix.name
    stem = posix.stem
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or ".test." in name
        or ".spec." in name
        or name.endswith("Test.java")
        or name.endswith("Tests.cs")
    )


def _is_generated_by_name(rel_path: str) -> bool:
    name = PurePosixPath(rel_path).name
    if name.endswith(_GENERATED_BASENAME_SUFFIXES):
        return True
    return any(marker in name for marker in _GENERATED_NAME_SUBSTRINGS)


def _is_generated_by_content(root: Path, rel_path: str) -> bool:
    """Bounded first-`_MAX_SCAN_BYTES`-bytes scan for a canonical
    "generated" marker — copies `entry_points.py::_python_main_guard_entry_points()`'s
    exact bounded-read/timeout-guarded pattern, not a new unbounded read.
    Only called for files this same build pass already opened for AST
    parsing (see `_build_file_intelligence`'s `scan_content` gate) — never
    adds a new read for a file this module wouldn't already be touching."""
    try:
        with open(to_long_path(root / rel_path), "rb") as fh:
            raw = fh.read(_MAX_SCAN_BYTES)
    except OSError:
        return False
    content = raw.decode("utf-8", errors="ignore")
    try:
        return bool(regex.search(_GENERATED_CONTENT_RE, content, timeout=_GENERATED_SCAN_TIMEOUT))
    except TimeoutError:
        return False


def _configuration_paths(profile: RepoProfile) -> set[str]:
    """Repo-relative paths already known to be configuration/lockfiles —
    reuses `RepoProfile.configuration_files`/`lockfiles` verbatim (no new
    detection). `DetectedItem.evidence` entries are always shaped
    `"<path> — <reason>"` (see `detectors/registry.py::_match_rule()`), so
    the path is everything before the first " — "."""
    paths: set[str] = set(profile.lockfiles)
    for item in profile.configuration_files:
        for evidence in item.evidence:
            paths.add(evidence.split(" — ", 1)[0])
    return paths


def _classify_kind(
    rel_path: str, root: Path, *, configuration_paths: set[str], scan_content: bool
) -> FileKind:
    """Evidence-based only — checked in priority order, first match wins.
    No "library"/"application" split: there is no existing deterministic
    signal for that distinction today (confirmed with the user)."""
    if _is_test_path(rel_path):
        return "test"
    if _is_generated_by_name(rel_path) or is_vendor_or_build_path(rel_path):
        return "generated"
    if scan_content and _is_generated_by_content(root, rel_path):
        return "generated"
    if rel_path in configuration_paths:
        return "configuration"
    return "source"


def _build_file_intelligence(
    root: Path,
    walk_result: WalkResult,
    symbol_files: dict[str, FileSymbols],
    graph_facts: dict[str, FileFacts],
    edges: list[Edge],
    fingerprints: dict[str, FileFingerprint],
    profile: RepoProfile,
) -> dict[str, FileIntelligenceEntry]:
    """Build the complete `file_intelligence.json` payload for every file
    this walk saw. Reuses everything already computed this same build pass
    (`walk_result`/`symbol_files`/`graph_facts`/`edges`/`fingerprints`/
    `profile`) — the only new work is `_classify_kind()`'s naming checks
    and, for AST-covered files only, one bounded content scan (see
    `_is_generated_by_content`'s own docstring)."""
    tiers = dashboard.importance_tiers(walk_result.files, edges)
    configuration_paths = _configuration_paths(profile)

    # import_out/import_in stay pure counts (`imports`/`imported_by` below,
    # unchanged since QB-079); import_out_paths is QB-080's addition — the
    # same loop also keeps *which* files each `import` edge resolved to, not
    # only how many. File-level only (edge.kind == "import"), never a
    # call/inherit/export edge and never edge.target_symbol — this must stay
    # a plain file-to-file list, or a future "improvement" toward
    # symbol-level detail here would silently reintroduce the
    # symbol_facts.json/graph_facts.json cost QB-080 was written specifically
    # to avoid on the search/read path.
    import_out: Counter[str] = Counter()
    import_in: Counter[str] = Counter()
    import_out_paths: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.kind == "import":
            import_out[edge.source_file] += 1
            if edge.target_file is not None:
                import_in[edge.target_file] += 1
                import_out_paths[edge.source_file].add(edge.target_file)

    entries: dict[str, FileIntelligenceEntry] = {}
    for rel_path in walk_result.files:
        file_symbols = symbol_files.get(rel_path)
        file_facts = graph_facts.get(rel_path)
        fingerprint = fingerprints.get(rel_path)

        language = (
            file_facts.language
            if file_facts is not None
            else (language_for_path(rel_path) or "unknown")
        )
        kind = _classify_kind(
            rel_path,
            root,
            configuration_paths=configuration_paths,
            scan_content=file_facts is not None,
        )

        entries[rel_path] = FileIntelligenceEntry(
            language=language,
            kind=kind,
            importance=tiers.get(rel_path, "Low"),
            imports=import_out.get(rel_path, 0),
            imported_by=import_in.get(rel_path, 0),
            entry_point=file_symbols is not None
            and any(s.is_entry_point for s in file_symbols.symbols),
            top_symbols=_primary_symbol_names(file_symbols),
            imported_files=sorted(import_out_paths.get(rel_path, set())),
            size=fingerprint.size if fingerprint is not None else 0,
            mtime_ns=fingerprint.mtime_ns if fingerprint is not None else 0,
        )
    return entries


# ---------------------------------------------------------------------------
# Combined symbols+graph extraction (QB-055).
#
# `symbols.build_symbol_index_with_facts()` and
# `graph.build_dependency_graph_with_facts()` each do a complete,
# independent walk-and-parse of every AST-covered file — correct and
# already-optimal when either runs alone (`quor symbols`/`quor graph`, via
# `build_symbol_index()`/`build_dependency_graph()`), but this orchestrator
# is the one caller that always needs *both* artifacts from the same build
# pass. Calling the two independently here read every file's source off
# disk twice and ran its symbol extractor over it twice for an identical
# result (same source, same extractor, same output) — pure waste, not a
# second, differently-scoped parse the way `graph.py`'s own
# symbols-vs-relationships two-parses-per-file already is (see that
# module's docstring). `_extract_combined_facts()` below does one read, one
# symbol-extractor call, and one relationship-extractor call per file, and
# `_build_combined_facts()`/`_refresh_combined_facts()` are this module's
# only callers of it, replacing what used to be two independent loops
# (a full walk here, and — before QB-055 — two more inside
# `_refresh_symbol_facts()`/`_refresh_graph_facts()` below) with one.
# ---------------------------------------------------------------------------


def _extract_combined_facts(
    root: Path, rel_path: str, language: str
) -> tuple[FileSymbols | None, FileFacts | None, SkipReason | None]:
    """Parse `rel_path` once and derive both its `FileSymbols` and
    `FileFacts` from that single read + single symbol-extractor call +
    single relationship-extractor call. Mirrors
    `symbols.extract_file_symbols()`'s and `graph.extract_file_facts()`'s
    read/parse/fail-open behavior exactly (same size cap, same "empty
    symbol list is not a failure" contract for `FileSymbols`, same "always
    return real `FileFacts` for anything that parsed" contract) — it is
    those two functions fused into one pass, not new behavior.

    `_SYMBOLS_MAX_FILE_SIZE_BYTES`/`_GRAPH_MAX_FILE_SIZE_BYTES` are the same
    value today (each module documents the coupling); gating the one read
    on `max()` of the two means a future divergence could only ever widen
    what gets read, never silently narrow one artifact's coverage without
    the other noticing.
    """
    abs_path = to_long_path(root / rel_path)
    try:
        if abs_path.stat().st_size > max(_SYMBOLS_MAX_FILE_SIZE_BYTES, _GRAPH_MAX_FILE_SIZE_BYTES):
            return None, None, "too_large"
        source = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None, "parse_failure"

    symbol_extractor = get_symbol_extractor(language)
    relationship_extractor = get_relationship_extractor(language)
    if symbol_extractor is None or relationship_extractor is None:
        # Unreachable in practice given the caller's precondition — mirrors
        # symbols.py's/graph.py's identical skip-not-raise guard.
        return None, None, "parse_failure"

    try:
        with warnings.catch_warnings():
            # A missing optional dependency is already reported once via
            # languages_skipped, not per-file — mirrors symbols.py's/
            # graph.py's identical reasoning.
            warnings.simplefilter("ignore")
            symbols = symbol_extractor(source)
            relationships = relationship_extractor(source)
    except Exception:  # noqa: BLE001 — per-file fail-open, mirrors symbols.py's/graph.py's identical boundary
        return None, None, "parse_failure"

    file_symbols = None
    if symbols:
        sorted_symbols = sorted(symbols, key=lambda s: (s.line, s.name))
        file_symbols = FileSymbols(path=rel_path, language=language, symbols=sorted_symbols)
    file_facts = FileFacts(
        language=language, symbol_counts=Counter(s.name for s in symbols), relationships=relationships
    )
    return file_symbols, file_facts, None


def _build_combined_facts(root: Path, walk_result: WalkResult) -> tuple[
    dict[str, FileSymbols],
    set[str],
    dict[str, FileFacts],
    set[str],
    set[str],
    set[str],
    int,
    int,
]:
    """Full-scan counterpart to `_refresh_combined_facts()` below — walks
    every file once and extracts both artifacts' facts per file via
    `_extract_combined_facts()`, replacing what used to be
    `build_symbol_index_with_facts()` and `build_dependency_graph_with_facts()`
    each doing their own full walk-and-parse (QB-055). `_SYMBOL_EXTRACTORS`/
    `_RELATIONSHIP_EXTRACTORS` in `ast_summarize/registry.py` are registered
    for exactly the same eight languages, so "language covered/skipped" is
    identical for both artifacts — computed once here rather than twice."""
    symbol_files: dict[str, FileSymbols] = {}
    symbol_parse_failures: set[str] = set()
    graph_facts: dict[str, FileFacts] = {}
    graph_parse_failures: set[str] = set()
    languages_covered: set[str] = set()
    languages_skipped: set[str] = set()
    large_file_count = 0
    parse_failure_count = 0

    for rel_path in walk_result.files:
        language = EXTENSION_TO_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower())
        if language is None:
            continue

        if not is_language_available(language):
            languages_skipped.add(language)
            continue

        if get_symbol_extractor(language) is None or get_relationship_extractor(language) is None:
            # Unreachable in practice — see _extract_combined_facts()'s identical guard.
            continue
        languages_covered.add(language)

        # Vendor/build-path and generated-by-name checks are cheap (no disk
        # access) and go before the one call in this loop that reads the
        # file (_extract_combined_facts) — skipping here avoids the read
        # entirely, not just the classification. The language itself is
        # still recorded as covered above: it's genuinely supported, this
        # one file is just not worth indexing as source.
        if is_vendor_or_build_path(rel_path) or _is_generated_by_name(rel_path):
            continue

        file_symbols, file_facts, reason = _extract_combined_facts(root, rel_path, language)
        if reason == "too_large":
            large_file_count += 1
            continue
        if reason == "parse_failure":
            parse_failure_count += 1
            symbol_parse_failures.add(rel_path)
            graph_parse_failures.add(rel_path)
            continue
        if file_symbols is not None:
            symbol_files[rel_path] = file_symbols
        if file_facts is not None:
            graph_facts[rel_path] = file_facts

    return (
        symbol_files,
        symbol_parse_failures,
        graph_facts,
        graph_parse_failures,
        languages_covered,
        languages_skipped,
        large_file_count,
        parse_failure_count,
    )


def _full_rebuild(
    root: Path, *, action: BuildAction, t0: float, echo: Echo, quor_version: str
) -> RepoIntelligence:
    banner = _ACTION_BANNERS.get(action)
    if banner:
        echo(banner)

    echo("Scanning repository...")
    walk_result = walk_repository(root)

    echo("Building repository intelligence...")
    profile = build_profile(root, walk_result=walk_result)

    echo("Building symbols and dependency graph...")
    (
        symbol_files,
        symbol_parse_failures,
        graph_facts,
        graph_parse_failures,
        languages_covered,
        languages_skipped,
        large_file_count,
        parse_failure_count,
    ) = _build_combined_facts(root, walk_result)

    symbol_index = assemble_symbol_index(
        root,
        walk_result,
        list(symbol_files.values()),
        languages_covered=languages_covered,
        languages_skipped=languages_skipped,
        large_file_count=large_file_count,
        parse_failure_count=parse_failure_count,
    )
    graph = assemble_graph(
        root,
        walk_result,
        graph_facts,
        languages_covered=languages_covered,
        languages_skipped=languages_skipped,
        large_file_count=large_file_count,
        parse_failure_count=parse_failure_count,
    )

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

    file_intelligence = _build_file_intelligence(
        root, walk_result, symbol_files, graph_facts, graph.edges, fingerprints, profile
    )
    intel_store.save_file_intelligence(root, file_intelligence)

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
        return _full_rebuild(
            root, action="corrupted_rebuild", t0=t0, echo=echo, quor_version=quor_version
        )
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

        echo("Building symbols and dependency graph...")
        symbol_files, symbol_parse_failures, graph_facts, graph_parse_failures = _refresh_combined_facts(
            root, diff, symbol_files, symbol_parse_failures, graph_facts, graph_parse_failures
        )

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

    if not diff.is_empty or intel_store.load_file_intelligence(root) is None:
        # Either fresh data (diff non-empty) or a one-time backfill for a
        # cache that predates file_intelligence.json / was built at an
        # older FILE_INTELLIGENCE_VERSION — never rewrites the other four
        # files, and a true cache-hit with an already-valid file skips
        # this entirely, preserving that branch's "nothing rebuilt"
        # contract for everything except this additive file.
        file_intelligence = _build_file_intelligence(
            root, walk_result, symbol_files, graph_facts, graph.edges, new_fingerprints, profile
        )
        intel_store.save_file_intelligence(root, file_intelligence)

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


def _refresh_combined_facts(
    root: Path,
    diff: RepoDiff,
    cached_symbol_files: dict[str, FileSymbols],
    cached_symbol_parse_failures: set[str],
    cached_graph_facts: dict[str, FileFacts],
    cached_graph_parse_failures: set[str],
) -> tuple[dict[str, FileSymbols], set[str], dict[str, FileFacts], set[str]]:
    """Incremental counterpart to `_build_combined_facts()` above — QB-055's
    fix for what used to be `_refresh_symbol_facts()`/`_refresh_graph_facts()`
    looping over `diff.reextraction_paths` independently, each re-reading
    and re-parsing the same changed file a second time for no benefit."""
    symbol_files = dict(cached_symbol_files)
    symbol_parse_failures = set(cached_symbol_parse_failures)
    graph_facts = dict(cached_graph_facts)
    graph_parse_failures = set(cached_graph_parse_failures)

    for old_path in diff.deleted:
        symbol_files.pop(old_path, None)
        symbol_parse_failures.discard(old_path)
        graph_facts.pop(old_path, None)
        graph_parse_failures.discard(old_path)

    for old_path, new_path in diff.renamed:
        # Pure rename: content is byte-identical to what the old path
        # already had cached — relocate, never re-parse (see
        # RepoDiff.reextraction_paths's docstring for the bug this fixed).
        moved_symbols = symbol_files.pop(old_path, None)
        was_symbol_failure = old_path in symbol_parse_failures
        symbol_parse_failures.discard(old_path)
        if moved_symbols is not None:
            symbol_files[new_path] = dataclasses.replace(moved_symbols, path=new_path)
        elif was_symbol_failure:
            symbol_parse_failures.add(new_path)
        # else: old_path had zero symbols and wasn't a failure either —
        # new_path correctly inherits that same "nothing to record" outcome.

        # FileFacts carries no `path` field of its own, unlike FileSymbols,
        # so this is a plain key move with no field to update.
        moved_facts = graph_facts.pop(old_path, None)
        was_graph_failure = old_path in graph_parse_failures
        graph_parse_failures.discard(old_path)
        if moved_facts is not None:
            graph_facts[new_path] = moved_facts
        elif was_graph_failure:
            graph_parse_failures.add(new_path)

    for rel_path in diff.reextraction_paths:
        symbol_files.pop(rel_path, None)
        symbol_parse_failures.discard(rel_path)
        graph_facts.pop(rel_path, None)
        graph_parse_failures.discard(rel_path)

        language = EXTENSION_TO_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower())
        if (
            language is None
            or not is_language_available(language)
            or get_symbol_extractor(language) is None
            or get_relationship_extractor(language) is None
        ):
            continue
        # Mirrors _build_combined_facts()'s identical pre-read skip — a
        # changed vendor/build-path or generated-by-name file still doesn't
        # belong in the index just because it was re-touched.
        if is_vendor_or_build_path(rel_path) or _is_generated_by_name(rel_path):
            continue
        file_symbols, file_facts, reason = _extract_combined_facts(root, rel_path, language)
        if reason == "parse_failure":
            symbol_parse_failures.add(rel_path)
            graph_parse_failures.add(rel_path)
            continue
        if file_symbols is not None:
            symbol_files[rel_path] = file_symbols
        if file_facts is not None:
            graph_facts[rel_path] = file_facts

    return symbol_files, symbol_parse_failures, graph_facts, graph_parse_failures


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
