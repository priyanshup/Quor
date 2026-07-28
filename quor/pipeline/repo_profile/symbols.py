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

from quor.pipeline.ast_summarize.registry import (
    extra_for_language,
    get_symbol_extractor,
    is_language_available,
)
from quor.pipeline.repo_profile.symbols_model import FileSymbols, RepoSymbolIndex
from quor.pipeline.repo_profile.walk import walk_repository

# Extension -> ast_summarize registry language key. Deliberately a fresh,
# purpose-built table scoped to exactly the languages a symbol extractor is
# registered for — not `languages.py`'s display-name census table (a
# different question: "what language is this file" vs. "can this file be
# symbol-parsed") and not `claude_read.py`'s private, filter-routing table
# (see `languages.py`'s own docstring for why importing a private,
# differently-scoped table from a different module would be the wrong
# reuse here — the same reasoning applies a second time).
_EXTENSION_TO_AST_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".cs": "csharp",
}

# Files larger than this are skipped, not parsed — a generated/minified/
# vendored file this size is far more likely to be pathological parse input
# than a hand-written source file worth indexing, and every parser here is
# O(file size) at best. Large-repo scaling was flagged as an explicit,
# unresolved risk in QB-061's own design doc (§7 risk 4) specifically for
# this future phase; this is that phase's answer — a fixed, documented cap,
# not silent unbounded parsing.
_MAX_FILE_SIZE_BYTES = 2_000_000


def build_symbol_index(root: Path) -> RepoSymbolIndex:
    """Scan `root` and return its deterministic RepoSymbolIndex.

    Calling this twice against unchanged repo state returns an identical
    RepoSymbolIndex (field-for-field) — the same core promise `quor map`'s
    `build_profile()` already makes, verified the same way (a dedicated
    determinism test, not just informal expectation).
    """
    walk_result = walk_repository(root)

    files: list[FileSymbols] = []
    languages_covered: set[str] = set()
    languages_skipped: set[str] = set()
    large_file_count = 0
    parse_failure_count = 0
    total_symbols = 0

    for rel_path in walk_result.files:
        language = _EXTENSION_TO_AST_LANGUAGE.get(PurePosixPath(rel_path).suffix.lower())
        if language is None:
            continue

        if not is_language_available(language):
            languages_skipped.add(language)
            continue

        abs_path = root / rel_path
        try:
            if abs_path.stat().st_size > _MAX_FILE_SIZE_BYTES:
                large_file_count += 1
                continue
            source = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            parse_failure_count += 1
            continue

        extractor = get_symbol_extractor(language)
        if extractor is None:
            # Unreachable in practice: is_language_available() already
            # confirmed `language` is registered. No `assert` here (banned
            # for validation-shaped checks project-wide) — an explicit,
            # skip-not-raise guard instead, consistent with every other
            # "this file just doesn't contribute" branch in this loop.
            continue
        languages_covered.add(language)

        try:
            with warnings.catch_warnings():
                # A missing optional dependency is already reported once via
                # languages_skipped above, not per-file — is_language_available()
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
            # index — mirrors `map_command._track_map_invocation()`'s
            # identical, explicitly-commented fail-open boundary.
            parse_failure_count += 1
            continue

        if symbols:
            symbols = sorted(symbols, key=lambda s: (s.line, s.name))
            files.append(FileSymbols(path=rel_path, language=language, symbols=symbols))
            total_symbols += len(symbols)

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
        total_symbols=total_symbols,
        notes=notes,
    )
