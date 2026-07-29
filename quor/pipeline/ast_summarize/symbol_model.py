"""`Symbol` — the per-language symbol-extraction data contract (QB-066).

A sibling capability to this package's existing `analyze_*()` compression
analyzers, not a replacement: `analyze_python()` etc. answer "which lines
are a compressible function body"; `extract_symbols_*()` (one per language
module, added alongside its existing `analyze_*()`) answers "what named,
locatable constructs does this file declare" — classes, interfaces,
structs, traits, enums, functions, and methods, each with its declaration
line, public/private visibility, and whether it looks like a program entry
point. Both question families parse the same source with the same
language's own parser; `extract_symbols_*()` never re-derives the
compression line-range logic and `analyze_*()` never derives symbol
identity, keeping the two independently correct.

`Symbol` is a plain, frozen dataclass — not a Pydantic model — because it
carries no external validation concern (every field is computed internally
from a parse tree, never user input); mirrors `quor/pipeline/repo_profile/
walk.py`'s `WalkResult` for the same reason. `quor/pipeline/repo_profile/
symbols_model.py` (a different, consuming package) wraps lists of these
into the `RepoSymbolIndex` that `quor symbols` renders — this module has no
knowledge of that consumer, the same one-directional-dependency discipline
`quor/pipeline/repo_profile/model.py`'s own docstring documents for
`DetectedItem`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ENTRY_POINT_NAMES = frozenset({"main", "Main"})
"""Shared by every language's `extract_symbols_*()` — see `Symbol.is_entry_point`'s
own docstring for why a plain name match against these two literals covers
every language this module supports."""

SymbolKind = Literal[
    "class",
    "interface",
    "struct",
    "trait",
    "enum",
    "function",
    "method",
]


@dataclass(frozen=True)
class Symbol:
    """One named, locatable construct declared in a single source file."""

    name: str
    kind: SymbolKind
    line: int
    """1-indexed line the declaration starts on."""

    is_public: bool
    """Language-specific, deterministically verifiable visibility:
    Python/JavaScript/TypeScript — not private-by-convention (no leading
    `_`/`#`) or, where the language has a real export/accessibility
    mechanism (JS/TS module exports, TS `private`/`protected` modifiers),
    that mechanism directly. Go — leading-capital identifier (the
    language's own exported-name rule). Java/Rust/C# — an explicit
    `public`/`pub` modifier is present (each language's own default without
    one is non-public: package-private for Java, private for Rust/C#)."""

    is_entry_point: bool = False
    """True for a function/method named exactly `main` (Python/JavaScript/
    TypeScript/Go/Java/Rust convention) or `Main` (C#'s own convention) —
    every mainstream entry-point convention among the languages this module
    supports names the entry function one of those two ways, so a plain
    name match is deterministic and needs no per-language special casing
    beyond the two literal names checked."""
