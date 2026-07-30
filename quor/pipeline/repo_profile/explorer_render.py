"""`quor explore` -> plain-text and JSON presentation (QB-078).

Plain, deterministic text by default (not Rich tables) — mirrors `quor
map`/`quor symbols`/`quor graph`'s convention rather than `quor repo`/`quor
gain`'s terminal-dashboard style, since QB-078's own spec frames this
command for two audiences at once ("developers and AI agents"): a fixed,
grep-friendly template reads equally well pasted into an AI's context or
read directly by a person, the same reasoning `render.py`/`symbols_render.py`/
`graph_render.py`'s own docstrings already give for their identical choice.
`--json` uses the same `dataclasses.asdict()` + `orjson` idiom every other
repository-intelligence command's JSON mode uses (`symbols_render.py`,
`graph_render.py`, `dashboard_render.py`) — every type in `explorer_model.py`
is a plain dataclass for exactly that reason. One `render_*_json()`/
`render_*_text()` pair per result type, mirroring those modules' own
one-function-per-artifact convention rather than a single polymorphic
renderer (`dataclasses.asdict()` needs a concrete dataclass type, not
`object`, to type-check cleanly under mypy).
"""

from __future__ import annotations

import dataclasses

import orjson

from quor.cli.format_utils import format_duration
from quor.pipeline.repo_profile.explorer_model import (
    CacheUnavailable,
    DependencyResult,
    FileSummary,
    RepoStats,
    SymbolFindResult,
    UsedByResult,
)


def render_cache_unavailable_json(error: CacheUnavailable) -> str:
    return orjson.dumps(dataclasses.asdict(error), option=orjson.OPT_INDENT_2).decode()


def render_cache_unavailable_text(error: CacheUnavailable) -> str:
    return error.message


def render_find_json(result: SymbolFindResult) -> str:
    return orjson.dumps(dataclasses.asdict(result), option=orjson.OPT_INDENT_2).decode()


def render_find_text(result: SymbolFindResult) -> str:
    """Renders the found/ambiguous cases only — an empty `result.matches`
    is a hard error the CLI layer reports itself (`explore.py::
    _exit_symbol_not_found()`), with its own stderr/exit-code handling, so
    it never reaches this presentation-only function."""
    if not result.is_ambiguous:
        m = result.matches[0]
        lines = [
            "Symbol",
            f"  {m.name}",
            "",
            "Defined in",
            f"  {m.path}",
            "",
            "Language",
            f"  {m.language}",
            "",
            "Type",
            f"  {m.kind.capitalize()}",
            "",
            "Exports",
            f"  {'Yes' if m.exports else 'No'}",
        ]
        return "\n".join(lines)

    lines = [result.query, f"{len(result.matches)} matches", ""]
    for i, m in enumerate(result.matches, start=1):
        lines.append(f"{i}. {m.path}")
    return "\n".join(lines)


def render_deps_json(result: DependencyResult) -> str:
    return orjson.dumps(dataclasses.asdict(result), option=orjson.OPT_INDENT_2).decode()


def render_deps_text(result: DependencyResult) -> str:
    lines = ["Direct dependencies", ""]
    if result.dependencies:
        lines.extend(result.dependencies)
    else:
        lines.append("(none)")
    lines.extend(["", f"Total: {result.total}"])
    return "\n".join(lines)


def render_used_by_json(result: UsedByResult) -> str:
    return orjson.dumps(dataclasses.asdict(result), option=orjson.OPT_INDENT_2).decode()


def render_used_by_text(result: UsedByResult) -> str:
    lines = ["Referenced by", ""]
    if result.used_by:
        lines.extend(result.used_by)
    else:
        lines.append("(none)")
    lines.extend(["", f"Total: {result.total}"])
    return "\n".join(lines)


def render_file_json(summary: FileSummary) -> str:
    return orjson.dumps(dataclasses.asdict(summary), option=orjson.OPT_INDENT_2).decode()


def render_file_text(summary: FileSummary) -> str:
    lines = [
        "Language",
        f"  {summary.language}",
        "",
        "Symbols",
        f"  {summary.symbol_count}",
        "",
        "Relationships",
        f"  {summary.relationship_count}",
        "",
        "Imports",
        f"  {summary.import_count}",
        "",
        "Exports",
        f"  {summary.export_count}",
        "",
        "Repository importance",
        f"  {summary.importance}",
    ]
    return "\n".join(lines)


_CACHE_STATUS_LABELS: dict[str, str] = {
    "fresh": "OK",
    "stale": "Stale (built by a different Quor version — run `quor repo --rebuild` to refresh)",
}


def render_stats_json(stats: RepoStats) -> str:
    return orjson.dumps(dataclasses.asdict(stats), option=orjson.OPT_INDENT_2).decode()


def render_stats_text(stats: RepoStats) -> str:
    lines = [
        "Repository",
        f"  {stats.name}",
        "",
        "Files",
        f"  {stats.total_files}",
        "",
        "Languages",
        f"  {stats.total_languages}",
        "",
        "Symbols",
        f"  {stats.total_symbols}",
        "",
        "Relationships",
        f"  {stats.total_relationships}",
        "",
        "Dependency edges",
        f"  {stats.dependency_edges}",
        "",
        "Largest file",
        f"  {stats.largest_file or '(none)'}",
        "",
        "Most imported file",
        f"  {stats.most_imported_file or '(none)'}",
        "",
        "Most referenced symbol",
        f"  {stats.most_referenced_symbol or '(none)'}",
        "",
        "Repository intelligence age",
        f"  {format_duration(stats.intelligence_age_seconds)} ago",
        "",
        "Cache status",
        f"  {_CACHE_STATUS_LABELS.get(stats.cache_status, stats.cache_status)}",
    ]
    return "\n".join(lines)
