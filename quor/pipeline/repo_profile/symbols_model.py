"""`RepoSymbolIndex` — the frozen data contract `quor symbols` renders
(QB-066).

Plain, frozen dataclasses, not Pydantic models — matching `walk.py`'s
`WalkResult`, not `model.py`'s `RepoProfile`: this data has no external
validation concern (every field is computed internally from a repo walk
plus a parse tree, never user input), and `orjson.dumps()` serializes
dataclass instances natively, so `--json` needs no extra conversion step.
`RepoProfile` uses Pydantic for a different reason — its fields are the
kind of thing a filter-config-like consumer might eventually validate
against; `RepoSymbolIndex` has no such consumer.

`Symbol` itself is `quor.pipeline.ast_summarize.symbol_model.Symbol` —
reused directly, not redefined here, since a symbol's shape (name, kind,
line, visibility, entry-point flag) is identical whether it came from a
single-file compression call or a whole-repo scan; only where the symbols
came from (`FileSymbols.path`) is new information this package adds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quor.pipeline.ast_summarize.symbol_model import Symbol

__all__ = ["FileSymbols", "RepoSymbolIndex", "Symbol"]


@dataclass(frozen=True)
class FileSymbols:
    """One source file's extracted symbols."""

    path: str
    """Repo-relative POSIX path."""

    language: str
    """The `ast_summarize` registry language key this file was parsed as
    (e.g. `"python"`, `"tsx"`) — not `languages.py`'s display name, since
    that table covers many languages with no symbol extractor at all."""

    symbols: list[Symbol] = field(default_factory=list)


@dataclass(frozen=True)
class RepoSymbolIndex:
    """The complete, deterministic repository symbol index."""

    root: str
    """The scanned root, as a POSIX path — for context in the rendered
    output, never used for identity/comparison (mirrors `RepoProfile.root`'s
    identical convention)."""

    files: list[FileSymbols] = field(default_factory=list)
    """Only files with at least one extracted symbol — an empty-symbol file
    (no classes/functions/etc. found, e.g. a pure side-effect script) is
    omitted rather than listed with nothing under it, the same token-lean
    convention `render.py` already applies to `RepoProfile`'s own empty
    sections. Sorted by `path`."""

    languages_covered: list[str] = field(default_factory=list)
    """Sorted `ast_summarize` language keys that had at least one file
    successfully scanned (even if that file happened to declare zero
    symbols)."""

    languages_skipped: list[str] = field(default_factory=list)
    """Sorted `ast_summarize` language keys that had at least one matching
    file but whose optional tree-sitter dependency isn't installed — see
    `notes` for the actionable `pip install` line naming each one."""

    total_symbols: int = 0

    notes: list[str] = field(default_factory=list)
    """Scope/limitation notes about this specific scan (missing optional
    dependencies, files skipped for exceeding the size cap, per-file parse
    failures) — never a compressed/invented summary, just deterministic
    facts about how the scan itself was performed. Mirrors
    `RepoProfile.notes`'s identical convention."""
