"""Orchestrator for the Repository Dependency Graph (QB-067, `quor graph`).

`build_dependency_graph()` is the single public entry point — mirrors
`symbols.py`'s `build_symbol_index()` shape exactly: walk the repo once
(`walk.py`, identical to `quor map`/`quor symbols`), and for every file
whose extension maps to an `ast_summarize`-registered language, parse it
once with that language's own parser and call **two** of that module's
functions — `get_symbol_extractor()` (QB-066, already-registered) and
`get_relationship_extractor()` (QB-067, new) — rather than re-walking the
repo or re-reading files a second time. This is a deliberate, one-loop
design, not two separate scans: reusing `build_symbol_index()` as an opaque
black box here would mean walking the repo and reading every file *twice*
(once inside it, once again in this module) to get both symbols and
relationships for the same file — strictly worse for "do not duplicate AST
parsing" than the two-parses-per-file this design already accepts (matching
the existing, already-shipped precedent that `analyze_*()` and
`extract_symbols_*()` are two independent parses of the same source when a
caller needs both — see `ast_summarize/registry.py`'s own module docstring).

**Per-file extractors emit raw, unresolved facts; this module is the one
place that resolves them.** Every `Relationship` `extract_relationships_*()`
returns is file-local (see `relationship_model.py`'s own docstring) — it
never names a specific file, only a raw name/path. Resolving a raw import
target to an actual repo file, and a raw base-class/callee name to a
specific symbol in a specific file, is a repository-wide question that
needs the *whole* walk's symbol table — so it happens exactly once, here,
rather than being re-derived (and risking eight different, drifting
answers) inside each of the eight language modules.

**Resolution is conservative and unambiguous-only, by design (QB-067's own
scoping decision, confirmed with the user before implementation began — see
`docs/final/DECISIONS.md` ADR-039).** An edge's `target_file`/`target_symbol`
are populated only when:
  - a raw base-class/interface/trait/override/call reference resolves to
    **exactly one** symbol — either declared in the same file, or reachable
    through that file's own, unambiguous import bindings (never a wildcard/
    star import, which could shadow an unknown number of names — see each
    language's own `extract_relationships_*()` docstring for why those are
    skipped at the source);
  - a raw import/re-export module path resolves to **exactly one** file
    under a real, spec-or-convention-grounded resolution rule for that
    language (Python's relative-import semantics, JS/TS's relative-path-
    plus-extension-probing convention, Java's package-to-directory
    convention, Rust's `crate::`-rooted `src/` convention) — never a
    general package-manager/build-system resolution algorithm (no
    `node_modules`, no `go.mod`, no Java classpath, no Cargo workspace
    resolution, no C# project references) — see `_resolve_import_target()`
    for the exact, per-language rule and its documented boundary.
Anything else — a dynamic/computed call, a deeper attribute chain, a
wildcard import, an unresolvable external/stdlib dependency, an ambiguous
same-file name collision — is left unresolved (`target_file`/`target_symbol`
stay `None`) rather than guessed. `target_raw` is always present regardless,
so the underlying fact is never lost even when unresolved (QB-067's own
"do not infer, do not use heuristics" constraint, applied the same way
`quor map`/`quor symbols` already apply it to their own detections).

**Memory (QB-071).** The one-loop-per-repo-walk shape above is unchanged —
still exactly one read and one parse-pair per file, never a second repo
walk. What changed is what's *retained* between the walk and resolution:
a file's full `Symbol` list used to be kept in `per_file` for the whole
scan and then re-derived into two more structures (`symbol_names`,
`symbol_counts`) inside `_resolve_all()` — three copies of the same names.
Now each file's symbols are reduced to a single `Counter[str]`
(`symbol_counts`, name -> occurrence count) the instant they're extracted,
and the full `Symbol` objects are never retained past that point (see
`_FileData`'s docstring). `_resolve_all()` also drains `per_file` via
`.pop()` as it resolves each file, releasing that file's `relationships`
list as soon as its edges are appended rather than only once the whole
resolution loop finishes. `Symbol`/`Relationship`/`Edge` are all
`slots=True` dataclasses for the same reason — see each model's own
docstring. None of this changes `RepoDependencyGraph`'s contents,
ordering, or the resolution rules above; it changes only how much of the
walk's intermediate data is alive at once. A genuine second repository
pass (re-walk + re-read + re-parse purely to defer relationship storage
further) was considered and rejected — see the QB-071 session notes for
the measured peak-memory breakdown that made it not worth the added
walk/parse cost and the two-pass restructuring of this module.
"""

from __future__ import annotations

import posixpath
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from quor.pipeline.ast_summarize.registry import (
    EXTENSION_TO_LANGUAGE,
    extra_for_language,
    get_relationship_extractor,
    get_symbol_extractor,
    is_language_available,
)
from quor.pipeline.ast_summarize.relationship_model import Relationship
from quor.pipeline.repo_profile._longpath import to_long_path
from quor.pipeline.repo_profile.graph_model import Edge, RepoDependencyGraph
from quor.pipeline.repo_profile.walk import WalkResult, walk_repository

SkipReason = Literal["too_large", "parse_failure"]

# Mirrors `symbols.py`'s identical cap and identical reasoning — a
# generated/minified/vendored file this size is far more likely to be
# pathological parse input than source worth graphing.
_MAX_FILE_SIZE_BYTES = 2_000_000

# `qualifier` values `extract_relationships_*()` may emit for a `calls`
# relationship that mean "resolve within the enclosing file's own symbols,
# not through an import binding" (Python/JS/TS/Rust's `self`, JS/TS's
# `this`, Java/Rust's `super`, C#'s `base`) — see module docstring's
# resolution rules and each language module's own `calls` documentation.
_SAME_FILE_CALL_QUALIFIERS = frozenset({"self", "this", "super", "base"})

_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


@dataclass(slots=True)
class _FileData:
    """Per-file state retained only until that file's own edges are
    resolved (QB-071) — see `_resolve_all()`, which pops each entry as it
    goes. No `symbols` field: a file's `Symbol` list is reduced to a
    `Counter[str]` of names (`symbol_counts`, below) the instant it's
    extracted and never otherwise retained — nothing downstream of
    `_resolve_all()` needs a symbol's kind/line/visibility, only whether a
    name exists in a file and how many times."""

    language: str
    relationships: list[Relationship]


@dataclass(frozen=True, slots=True)
class FileFacts:
    """One file's raw, pre-resolution facts (QB-072) — everything
    `assemble_graph()` needs to resolve that file's edges, without
    re-parsing it. This is the unit the incremental repository-intelligence
    cache (`intel.py`) persists to disk and reuses across runs for every
    file that hasn't changed, re-deriving it via `extract_file_facts()`
    only for files that were added, modified, or renamed."""

    language: str
    symbol_counts: Counter[str]
    relationships: list[Relationship]


def extract_file_facts(root: Path, rel_path: str, language: str) -> tuple[FileFacts | None, SkipReason | None]:
    """Parse exactly one file and return its raw `FileFacts`, factored out
    of `build_dependency_graph()`'s own loop body (QB-072) so the
    incremental cache can re-parse a single changed file without
    re-walking or re-parsing the rest of the repo.

    Assumes the caller already confirmed `language` is available and has
    registered both a symbol and a relationship extractor
    (`is_language_available()`) — mirrors `build_dependency_graph()`'s own
    precondition for reaching this point. Unlike `symbols.extract_file_symbols`,
    a file that parses successfully but declares zero symbols/relationships
    still returns real `FileFacts` (never `(None, None)`) — it may still be
    a resolution *target* for another file's import/call even if it has no
    outgoing relationships of its own, mirroring `build_dependency_graph()`'s
    existing behavior of always retaining every successfully-parsed file.
    """
    abs_path = to_long_path(root / rel_path)
    try:
        if abs_path.stat().st_size > _MAX_FILE_SIZE_BYTES:
            return None, "too_large"
        source = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "parse_failure"

    symbol_extractor = get_symbol_extractor(language)
    relationship_extractor = get_relationship_extractor(language)
    if symbol_extractor is None or relationship_extractor is None:
        # Unreachable in practice: is_language_available() already
        # confirmed `language` is registered for both extractor families
        # (registry.py registers them together). No `assert` here (banned
        # project-wide for validation-shaped checks) — an explicit,
        # skip-not-raise guard instead, mirroring `symbols.py`'s identical
        # "this file just doesn't contribute" branch.
        return None, "parse_failure"

    try:
        with warnings.catch_warnings():
            # A missing optional dependency is already reported once via
            # languages_skipped, not per-file — see `symbols.py`'s
            # identical reasoning.
            warnings.simplefilter("ignore")
            symbols = symbol_extractor(source)
            relationships = relationship_extractor(source)
    except Exception:  # noqa: BLE001 — per-file fail-open, see module docstring
        # Mirrors `symbols.py`'s identical per-file fail-open boundary:
        # a repo-wide scan spans arbitrarily many, only partially-trusted
        # source files, with no engine above this loop the way a
        # single-file compression call has (ADR-018). One malformed file
        # must not abort the whole graph.
        return None, "parse_failure"

    # QB-071: reduce to a name -> count aggregate immediately — nothing
    # downstream needs a symbol's kind/line/visibility, only whether a name
    # exists in a file and how many times. `symbols` (the full Symbol list)
    # is never retained past this point.
    return FileFacts(
        language=language,
        symbol_counts=Counter(s.name for s in symbols),
        relationships=relationships,
    ), None


def assemble_graph(
    root: Path,
    walk_result: WalkResult,
    facts: dict[str, FileFacts],
    *,
    languages_covered: set[str],
    languages_skipped: set[str],
    large_file_count: int,
    parse_failure_count: int,
) -> RepoDependencyGraph:
    """Resolve an already-assembled `facts` map into the final
    `RepoDependencyGraph` — the shared tail both `build_dependency_graph()`
    (a fresh full scan) and the incremental cache's merge path (QB-072,
    `intel.py`) use, so the resolution-and-notes logic exists exactly once.

    `facts` itself is never mutated or drained — internally, a *separate*
    `per_file`/`symbol_counts` view is built from it, and that view (not
    `facts`) is what `_resolve_all()` pops from as it resolves each file
    (QB-071's memory discipline, unchanged). `facts` remains valid and
    intact for the caller after this function returns.
    """
    per_file = {path: _FileData(language=f.language, relationships=f.relationships) for path, f in facts.items()}
    symbol_counts = {path: f.symbol_counts for path, f in facts.items()}
    edges = _resolve_all(per_file, symbol_counts)

    notes: list[str] = []
    if not walk_result.used_git:
        notes.append(
            "Not a git repository (or git is unavailable) — used a filesystem "
            "walk with a hardcoded ignore list instead of `git ls-files`; "
            "node_modules/.venv/build artifacts may be under-filtered compared "
            "to a real git-tracked scan."
        )
    for language in sorted(languages_skipped):
        extra = extra_for_language(language)
        install_hint = f' — install `pip install "quor[{extra}]"`' if extra else ""
        notes.append(f"{language} files were found but skipped (missing optional dependency{install_hint}).")
    if large_file_count:
        plural = "file" if large_file_count == 1 else "files"
        notes.append(
            f"{large_file_count} {plural} exceeded the "
            f"{_MAX_FILE_SIZE_BYTES // 1_000_000}MB size cap and were skipped."
        )
    if parse_failure_count:
        plural = "file" if parse_failure_count == 1 else "files"
        notes.append(f"{parse_failure_count} {plural} could not be read or parsed and were skipped.")
    notes.append(
        "Cross-file resolution is conservative by design: an edge is resolved "
        "only when its target is unambiguous (see `quor graph --json`'s "
        "target_file/target_symbol fields). Wildcard imports, dynamic/"
        "computed calls, and external/stdlib dependencies are reported as "
        "raw facts but never resolved to a repo file."
    )

    edges.sort(key=lambda e: (e.source_file, e.line, e.kind, e.target_raw))
    return RepoDependencyGraph(
        root=root.as_posix(),
        edges=edges,
        languages_covered=sorted(languages_covered),
        languages_skipped=sorted(languages_skipped),
        total_edges=len(edges),
        resolved_edges=sum(1 for e in edges if e.target_file is not None),
        notes=notes,
    )


def build_dependency_graph_with_facts(
    root: Path, *, walk_result: WalkResult | None = None
) -> tuple[RepoDependencyGraph, dict[str, FileFacts], set[str]]:
    """Like `build_dependency_graph()`, but also returns every successfully-
    parsed file's raw `FileFacts` and the set of paths that failed to parse
    (QB-072) — the incremental repository-intelligence cache persists both
    so a later run can re-resolve the graph after re-parsing only the
    files that actually changed, without re-walking or re-parsing the rest
    of the repo.

    This is a deliberate, bounded trade-off specific to this entry point:
    `facts` keeps every file's `relationships` list reachable until this
    function returns (so it can be serialized to disk), a little past the
    point `assemble_graph()`'s own `_resolve_all()` call would otherwise
    let each entry be released (QB-071's per-file-drain-during-resolution
    discipline, which still applies — see `assemble_graph()`'s docstring).
    Every other caller — including `build_dependency_graph()` below — never
    retains this dict, so their memory profile is unaffected.

    `walk_result` (QB-072 perf follow-up): pass an already-computed
    `WalkResult` to skip a redundant `walk_repository()` call — see
    `profiler.build_profile()`'s identical parameter for the full
    rationale. Every existing caller that omits it is unaffected.
    """
    walk_result = walk_result if walk_result is not None else walk_repository(root)

    facts: dict[str, FileFacts] = {}
    parse_failures: set[str] = set()
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
            # Unreachable in practice — see extract_file_facts()'s identical guard.
            continue
        languages_covered.add(language)

        file_facts, reason = extract_file_facts(root, rel_path, language)
        if reason == "too_large":
            large_file_count += 1
            continue
        if reason == "parse_failure":
            parse_failure_count += 1
            parse_failures.add(rel_path)
            continue
        if file_facts is not None:
            facts[rel_path] = file_facts

    graph = assemble_graph(
        root,
        walk_result,
        facts,
        languages_covered=languages_covered,
        languages_skipped=languages_skipped,
        large_file_count=large_file_count,
        parse_failure_count=parse_failure_count,
    )
    return graph, facts, parse_failures


def build_dependency_graph(root: Path) -> RepoDependencyGraph:
    """Scan `root` and return its deterministic RepoDependencyGraph.

    Calling this twice against unchanged repo state returns an identical
    RepoDependencyGraph (field-for-field) — the same core promise
    `quor map`/`quor symbols` already make, verified the same way (a
    dedicated determinism test).
    """
    graph, _facts, _parse_failures = build_dependency_graph_with_facts(root)
    return graph


def _resolve_all(per_file: dict[str, _FileData], symbol_counts: dict[str, Counter[str]]) -> list[Edge]:
    """Resolve every file's raw relationships into `Edge`s.

    QB-071: `per_file` is drained via `.pop()` as each file is processed —
    not merely iterated — so a file's `relationships` list (and the
    `_FileData` wrapping it) is released for GC the moment its edges are
    appended, rather than staying alive until this whole function returns.
    `list(per_file.keys())` is snapshotted first purely so the loop can
    mutate `per_file` while iterating; it preserves the exact same order
    `per_file.items()` would have (both reflect insertion order), and the
    final `edges.sort()` in `build_dependency_graph()` makes output order
    independent of processing order regardless.

    `symbol_counts` (name -> occurrence count per file) does the job the
    old `symbol_names`/`symbol_counts` pair together used to: `name in
    symbol_counts.get(file, Counter())` is an identical membership check to
    the old `name in symbol_names.get(file, ())` (a `Counter` is a `dict`,
    and `in` tests keys either way), so `symbol_names` was a redundant
    second copy of the same names `symbol_counts` already had — dropped
    rather than kept as a separate structure.
    """
    all_files = frozenset(per_file)

    edges: list[Edge] = []
    for path in list(per_file.keys()):
        data = per_file.pop(path)
        bindings: dict[str, str] = {}
        for rel in data.relationships:
            if rel.kind != "import":
                continue
            resolved_file = _resolve_import_target(data.language, rel.target, path, all_files)
            if rel.qualifier is not None and resolved_file is not None:
                bindings[rel.qualifier] = resolved_file
            edges.append(
                Edge(
                    kind="import",
                    source_file=path,
                    target_raw=rel.target,
                    line=rel.line,
                    qualifier=rel.qualifier,
                    target_file=resolved_file,
                )
            )

        for rel in data.relationships:
            if rel.kind in ("import",):
                continue
            if rel.kind == "export":
                target_file = (
                    _resolve_import_target(data.language, rel.target, path, all_files) if rel.target else None
                )
                edges.append(
                    Edge(
                        kind="export",
                        source_file=path,
                        source_symbol=rel.source,
                        target_raw=rel.target,
                        line=rel.line,
                        target_file=target_file,
                    )
                )
                continue

            target_file, target_symbol = _resolve_relationship(rel, path, symbol_counts, bindings)
            edges.append(
                Edge(
                    kind=rel.kind,
                    source_file=path,
                    source_symbol=rel.source or None,
                    target_raw=rel.target,
                    line=rel.line,
                    qualifier=rel.qualifier,
                    target_file=target_file,
                    target_symbol=target_symbol,
                )
            )

    return edges


def _resolve_type_reference(
    target: str, file: str, symbol_counts: dict[str, Counter[str]], bindings: dict[str, str]
) -> tuple[str | None, str | None]:
    """Resolve a raw, possibly-dotted base-type/interface/trait reference
    (`"Base"`, `"ns.Base"`) to `(file, symbol)` — same-file first, then via
    the file's own single-segment import bindings. Never guessed: a dotted
    reference whose first segment isn't a known import binding, or whose
    resolved file doesn't actually declare the named symbol, stays
    unresolved.

    Membership is checked directly against `symbol_counts` (a `Counter`,
    i.e. a `dict`) rather than a separate `symbol_names` set — `name in
    a_dict` and `name in a_set` are the same O(1) key-presence test, so a
    second, set-shaped copy of the same names added nothing (QB-071)."""
    if "." in target:
        first_segment, _, _rest = target.partition(".")
        name = target.rsplit(".", 1)[-1]
        resolved_file = bindings.get(first_segment)
        if resolved_file is not None and name in symbol_counts.get(resolved_file, Counter()):
            return resolved_file, name
        return None, None

    if target in symbol_counts.get(file, Counter()):
        return file, target
    resolved_file = bindings.get(target)
    if resolved_file is not None and target in symbol_counts.get(resolved_file, Counter()):
        return resolved_file, target
    return None, None


def _resolve_relationship(
    rel: Relationship,
    file: str,
    symbol_counts: dict[str, Counter[str]],
    bindings: dict[str, str],
) -> tuple[str | None, str | None]:
    if rel.kind in ("inherits", "implements_interface", "implements_trait"):
        return _resolve_type_reference(rel.target, file, symbol_counts, bindings)

    if rel.kind == "overrides":
        if rel.qualifier is None:
            return None, None
        superclass_file, _ = _resolve_type_reference(rel.qualifier, file, symbol_counts, bindings)
        if superclass_file is not None and rel.target in symbol_counts.get(superclass_file, Counter()):
            return superclass_file, rel.target
        return None, None

    if rel.kind == "calls":
        if rel.qualifier is None:
            if symbol_counts.get(file, Counter())[rel.target] == 1:
                return file, rel.target
            resolved_file = bindings.get(rel.target)
            if resolved_file is not None and rel.target in symbol_counts.get(resolved_file, Counter()):
                return resolved_file, rel.target
            return None, None
        if rel.qualifier in _SAME_FILE_CALL_QUALIFIERS:
            if symbol_counts.get(file, Counter())[rel.target] == 1:
                return file, rel.target
            return None, None
        resolved_file = bindings.get(rel.qualifier)
        if resolved_file is not None and rel.target in symbol_counts.get(resolved_file, Counter()):
            return resolved_file, rel.target
        return None, None

    return None, None


def _resolve_import_target(
    language: str, target: str, importing_file: str, all_files: frozenset[str]
) -> str | None:
    """Resolve a raw import/re-export module specifier to a repo-relative
    file path, using each language's own real resolution rule — never a
    general package-manager/build-system algorithm. See module docstring
    for the exact boundary. Go and C# have no in-scope resolution rule at
    all (see `go.py`/`csharp.py`'s own `imports` docstrings) and always
    return `None`."""
    if language == "python":
        return _resolve_python_import(target, importing_file, all_files)
    if language in ("javascript", "typescript", "tsx"):
        return _resolve_js_import(target, importing_file, all_files)
    if language == "java":
        return _resolve_java_import(target, all_files)
    if language == "rust":
        return _resolve_rust_import(target, all_files)
    return None


def _resolve_python_import(target: str, importing_file: str, all_files: frozenset[str]) -> str | None:
    """Python's own relative-import semantics (`level` = leading dot
    count) resolved against the importing file's own directory, or a
    bounded absolute-import check against the repo root — never
    `sys.path`/venv/site-packages resolution (see module docstring)."""
    level = len(target) - len(target.lstrip("."))
    module_part = target[level:]
    segments = [s for s in module_part.split(".") if s]

    if level > 0:
        base_dir = PurePosixPath(importing_file).parent
        for _ in range(level - 1):
            base_dir = base_dir.parent
        if not segments:
            candidate = (base_dir / "__init__.py").as_posix()
            return candidate if candidate in all_files else None
        target_path = base_dir.joinpath(*segments)
    else:
        if not segments:
            return None
        target_path = PurePosixPath(*segments)

    for candidate in (f"{target_path.as_posix()}.py", (target_path / "__init__.py").as_posix()):
        if candidate in all_files:
            return candidate
    return None


def _resolve_js_import(target: str, importing_file: str, all_files: frozenset[str]) -> str | None:
    """Relative-path-plus-extension-probing, matching Node/bundler
    convention for a relative specifier — never `node_modules`/`tsconfig`
    `paths` resolution (see module docstring). A bare specifier (no
    leading `.`) is never resolved."""
    if not target.startswith("."):
        return None
    base_dir = posixpath.dirname(importing_file)
    joined = posixpath.normpath(posixpath.join(base_dir, target))

    candidates: list[str] = []
    if posixpath.splitext(joined)[1]:
        candidates.append(joined)
    candidates.extend(joined + ext for ext in _JS_EXTENSIONS)
    candidates.extend(posixpath.join(joined, "index" + ext) for ext in _JS_EXTENSIONS)

    for candidate in candidates:
        if candidate in all_files:
            return candidate
    return None


def _resolve_java_import(target: str, all_files: frozenset[str]) -> str | None:
    """Java's own package-to-directory convention: a fully-qualified name
    `a.b.C` must live at a path ending in `a/b/C.java`, regardless of
    which source root (`src/main/java/`, `src/`, repo root, ...) precedes
    it — never a build-tool-specific source-root list (see module
    docstring). Unresolved if zero or more than one walked file matches
    (ambiguous)."""
    suffix = "/".join(target.split(".")) + ".java"
    matches = [f for f in all_files if f == suffix or f.endswith(f"/{suffix}")]
    return matches[0] if len(matches) == 1 else None


def _resolve_rust_import(target: str, all_files: frozenset[str]) -> str | None:
    """Only `crate::`-rooted paths, resolved against the conventional
    single-crate `src/` layout (`crate::a::b::item` -> `src/a/b.rs` or
    `src/a/b/mod.rs`; a bare `crate::item` -> `src/lib.rs`/`src/main.rs`)
    — never a `Cargo.toml`-driven workspace/multi-crate resolution, and
    never `self::`/`super::` (both require knowing the importing file's
    own position in the module tree, which `mod` declarations establish
    separately from `use` paths — out of scope, see `rust.py`'s own
    `imports` docstring)."""
    if not target.startswith("crate::"):
        return None
    segments = target[len("crate::") :].split("::")
    module_segments = segments[:-1]
    if not module_segments:
        for candidate in ("src/lib.rs", "src/main.rs"):
            if candidate in all_files:
                return candidate
        return None
    joined = "/".join(module_segments)
    for candidate in (f"src/{joined}.rs", f"src/{joined}/mod.rs"):
        if candidate in all_files:
            return candidate
    return None
