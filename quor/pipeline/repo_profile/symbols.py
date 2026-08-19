"""Orchestrator for the Repository Symbols index (QB-066, `quor symbols`).

`build_symbol_index()` is the single public entry point: walk the repo once
(reusing `walk.py`, identical to `quor map`), and for every file whose
extension maps to an `ast_summarize`-registered language, parse it with
that language's own parser and extract its declared symbols via
`ast_summarize.registry.get_symbol_extractor()` — the same per-language
tree-sitter/`ast` parsing infrastructure `code_ast_summarize`/
`python_ast_summarize` already build, reused for a second, additive
purpose (see `quor/pipeline/ast_summarize/symbol_model.py`'s own docstring
for why this is a sibling capability, not a rewrite, of the existing
compression analyzers). No LLM, no network, no file content read outside
the walked file list itself.

Deliberately a separate command/index from `quor map`'s `RepoProfile`
(QB-061), not a new field on it: a symbol index scales with source line
count, not repo metadata size, so folding it into `quor map`'s default
output would make every `quor map` call pay a much larger, language-parse
cost for information most orientation calls don't need — see this
package's own `__init__.py` docstring and ADR-038 for the full reasoning
the two-command split follows.
"""

from __future__ import annotations

import warnings
from pathlib import Path, PurePosixPath
from typing import Literal

from quor.pipeline.ast_summarize.registry import (
    EXTENSION_TO_LANGUAGE,
    extra_for_language,
    get_symbol_extractor,
    is_language_available,
)
from quor.pipeline.repo_profile._longpath import to_long_path
from quor.pipeline.repo_profile.symbols_model import FileSymbols, RepoSymbolIndex
from quor.pipeline.repo_profile.walk import WalkResult, walk_repository

SkipReason = Literal["too_large", "parse_failure"]

# QB-067 promoted this table to `ast_summarize/registry.py::EXTENSION_TO_LANGUAGE`
# so `quor graph`'s orchestrator (`repo_profile/graph.py`) shares exactly
# one source of truth with this module rather than a second, drifting
# copy — kept as a local alias so every reference below is unchanged.
_EXTENSION_TO_AST_LANGUAGE = EXTENSION_TO_LANGUAGE

# Files larger than this are skipped, not parsed — a generated/minified/
# vendored file this size is far more likely to be pathological parse input
# than a hand-written source file worth indexing, and every parser here is
# O(file size) at best. Large-repo scaling was flagged as an explicit,
# unresolved risk in QB-061's own design doc (§7 risk 4) specifically for
# this future phase; this is that phase's answer — a fixed, documented cap,
# not silent unbounded parsing.
_MAX_FILE_SIZE_BYTES = 2_000_000


def extract_file_symbols(
    root: Path, rel_path: str, language: str
) -> tuple[FileSymbols | None, SkipReason | None]:
    """Parse exactly one file and return its symbols, factored out of
    `build_symbol_index()`'s own loop body (QB-072) so the incremental
    repository-intelligence cache (`quor/pipeline/repo_profile/intel.py`)
    can re-parse a single changed file without re-walking or re-scanning
    the rest of the repo.

    Assumes the caller already confirmed `language` is available and has a
    registered symbol extractor (`is_language_available()`) — mirrors
    `build_symbol_index()`'s own precondition for reaching this point.

    Returns `(None, None)` for a file that parsed successfully but declared
    zero symbols (a valid, common outcome — e.g. a pure side-effect script)
    — distinct from `(None, "parse_failure")`, which means the file could
    not be read or parsed at all. This distinction is what lets the
    incremental cache recompute `RepoSymbolIndex.notes`'s parse-failure
    count correctly after only re-scanning changed files (see `intel.py`'s
    own module docstring).
    """
    abs_path = to_long_path(root / rel_path)
    try:
        if abs_path.stat().st_size > _MAX_FILE_SIZE_BYTES:
            return None, "too_large"
        source = abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "parse_failure"

    extractor = get_symbol_extractor(language)
    if extractor is None:
        # Unreachable in practice given the caller's precondition — an
        # explicit, skip-not-raise guard (no `assert` for validation-shaped
        # checks project-wide), consistent with every other "this file
        # just doesn't contribute" branch here.
        return None, "parse_failure"

    try:
        with warnings.catch_warnings():
            # A missing optional dependency is already reported once via
            # languages_skipped, not per-file — is_language_available()
            # guarantees the dependency is present by the time we reach
            # here, so any warning the extractor might still emit would be
            # redundant, not informative.
            warnings.simplefilter("ignore")
            symbols = extractor(source)
    except Exception:  # noqa: BLE001 — per-file fail-open, see module docstring
        # A repo-wide scan spans arbitrarily many, only partially-trusted
        # source files; unlike a single-file compression call (which
        # relies on Pipeline.execute()'s own per-stage fail-open,
        # ADR-018), there is no engine above this loop to catch a parse
        # failure for us. One malformed file must not abort the whole
        # index — mirrors `quor/tracking/db.py::track_invocation_safe()`'s
        # identical, explicitly-commented fail-open boundary.
        return None, "parse_failure"

    if not symbols:
        return None, None
    symbols = sorted(symbols, key=lambda s: (s.line, s.name))
    return FileSymbols(path=rel_path, language=language, symbols=symbols), None


def assemble_symbol_index(
    root: Path,
    walk_result: WalkResult,
    files: list[FileSymbols],
    *,
    languages_covered: set[str],
    languages_skipped: set[str],
    large_file_count: int,
    parse_failure_count: int,
) -> RepoSymbolIndex:
    """Build the final `RepoSymbolIndex` from an already-assembled file list
    plus bookkeeping counts — the shared tail both `build_symbol_index()`
    (a fresh full scan) and the incremental cache's merge path (QB-072,
    `intel.py`) use, so the notes-rendering logic exists exactly once."""
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

    return RepoSymbolIndex(
        root=root.as_posix(),
        files=sorted(files, key=lambda f: f.path),
        languages_covered=sorted(languages_covered),
        languages_skipped=sorted(languages_skipped),
        total_symbols=sum(len(f.symbols) for f in files),
        notes=notes,
    )


def build_symbol_index_with_facts(
    root: Path, *, walk_result: WalkResult | None = None
) -> tuple[RepoSymbolIndex, dict[str, FileSymbols], set[str]]:
    """Like `build_symbol_index()`, but also returns every successfully-
    parsed file's `FileSymbols` (keyed by path) and the set of paths that
    failed to parse (QB-072) — the incremental repository-intelligence
    cache (`intel.py`) persists both so a later run can recompute
    `RepoSymbolIndex` for only the files that changed, without re-parsing
    the rest of the repo.

    `walk_result` (QB-072 perf follow-up): pass an already-computed
    `WalkResult` to skip a redundant `walk_repository()` call — see
    `profiler.build_profile()`'s identical parameter for the full
    rationale. Every existing caller that omits it is unaffected.
    """
    walk_result = walk_result if walk_result is not None else walk_repository(root)

    files: dict[str, FileSymbols] = {}
    parse_failures: set[str] = set()
    languages_covered: set[str] = set()
    languages_skipped: set[str] = set()
    large_file_count = 0
    parse_failure_count = 0

    for rel_path in walk_result.files:
        language = _EXTENSION_TO_AST_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower())
        if language is None:
            continue

        if not is_language_available(language):
            languages_skipped.add(language)
            continue

        if get_symbol_extractor(language) is None:
            # Unreachable in practice: is_language_available() already
            # confirmed `language` is registered for a symbol extractor —
            # an explicit, skip-not-raise guard, not an `assert`.
            continue
        languages_covered.add(language)

        file_symbols, reason = extract_file_symbols(root, rel_path, language)
        if reason == "too_large":
            large_file_count += 1
            continue
        if reason == "parse_failure":
            parse_failure_count += 1
            parse_failures.add(rel_path)
            continue
        if file_symbols is not None:
            files[rel_path] = file_symbols

    index = assemble_symbol_index(
        root,
        walk_result,
        list(files.values()),
        languages_covered=languages_covered,
        languages_skipped=languages_skipped,
        large_file_count=large_file_count,
        parse_failure_count=parse_failure_count,
    )
    return index, files, parse_failures


def build_symbol_index(root: Path) -> RepoSymbolIndex:
    """Scan `root` and return its deterministic RepoSymbolIndex.

    Calling this twice against unchanged repo state returns an identical
    RepoSymbolIndex (field-for-field) — the same core promise `quor map`'s
    `build_profile()` already makes, verified the same way (a dedicated
    determinism test, not just informal expectation).
    """
    index, _files, _parse_failures = build_symbol_index_with_facts(root)
    return index
