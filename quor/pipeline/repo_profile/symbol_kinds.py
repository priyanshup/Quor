"""Live, single-file AST symbol-kind classification for MCP payload
enrichment — answers "what kind of declaration is this" (class, interface,
struct, trait, enum, function, method — `SymbolKind`, QB-066) for the
`top_symbols` names `get_repo_context` already shows.

Deliberately **not** backed by `symbol_facts.json`: that cache is a
repo-wide file (`intel_store.load_symbol_facts()` loads every file's
symbols at once — no per-file lookup), and `search.py`'s own module
docstring already establishes why paying that O(repo-size) cost on a path
that only ever needs one file's data is the wrong trade. This module
instead parses exactly the one file a caller already has open, via the
same per-language `get_symbol_extractor()` registry `quor symbols`'s
repo-wide walk uses internally — O(one file), no cache dependency, never
stale.
"""

from __future__ import annotations

from pathlib import Path

from quor.pipeline.ast_summarize.registry import (
    EXTENSION_TO_LANGUAGE,
    get_symbol_extractor,
    is_language_available,
)
from quor.pipeline.ast_summarize.symbol_model import Symbol


def extract_file_symbols(abs_path: Path) -> list[Symbol]:
    """Parse `abs_path` fresh and return its symbols, or `[]` if its
    extension has no registered/available extractor, it can't be read, or
    parsing fails. Fail-open by design: unlike `code_ast_summarize.py`'s
    `analyze_*()` call (which runs inside `Pipeline.execute()`'s own
    per-stage ADR-018 safety net), nothing upstream of an MCP tool call
    catches a parser exception for this — a classification failure here
    must never be able to break the tool response it's only meant to
    enrich, so this function absorbs it internally instead.
    """
    language = EXTENSION_TO_LANGUAGE.get(abs_path.suffix.lower())
    if language is None or not is_language_available(language):
        return []
    extractor = get_symbol_extractor(language)
    if extractor is None:
        return []
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
        return extractor(source)
    except Exception:  # noqa: BLE001 — fail-open: a bad parse must never break the caller
        return []


def symbol_kind_by_name(symbols: list[Symbol]) -> dict[str, str]:
    """First-declaration-wins `name -> kind` map — mirrors
    `FileIntelligenceEntry.top_symbols`' own "first occurrence, in
    declaration-line order" dedup convention, so a caller enriching one of
    those names never sees a second, later declaration silently override
    the kind that name's own first (and only shown) declaration actually
    has."""
    result: dict[str, str] = {}
    for symbol in symbols:
        result.setdefault(symbol.name, symbol.kind)
    return result
