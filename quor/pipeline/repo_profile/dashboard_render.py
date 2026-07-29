"""RepoDashboard -> terminal presentation (Rich tables) and JSON (QB-076).

Presentation only — every number here already exists on `RepoDashboard`
(see `dashboard.py`/`dashboard_model.py` for where each field comes from);
this module only decides how to lay it out, mirroring `cli/format_utils.py`'s
"never influences what gets computed" discipline. Follows `quor gain`
(`quor/cli/commands/gain.py`)'s existing Rich `Console`/`Table` style rather
than the plain-Markdown template `render.py`/`symbols_render.py`/
`graph_render.py` use for `quor map`/`quor symbols`/`quor graph` — this
command's own spec calls for a "premium terminal dashboard" meant to be
read directly by a person, not a document meant to be pasted into an AI's
context the way the other three commands' output is.
"""

from __future__ import annotations

import dataclasses

import orjson
from rich.console import Console
from rich.table import Table

from quor.cli.format_utils import format_count, format_duration, format_percentage
from quor.pipeline.repo_profile.dashboard_model import RepoDashboard


def render_json(dashboard: RepoDashboard) -> str:
    """JSON output mode (`--json`) — identical information to the terminal
    dashboard, mirroring `quor map`/`quor symbols`/`quor graph`'s own
    `render_json()` convention (`dataclasses.asdict()` + orjson, since
    `RepoDashboard`'s whole tree is plain dataclasses)."""
    return orjson.dumps(dataclasses.asdict(dashboard), option=orjson.OPT_INDENT_2).decode()


def print_dashboard(dashboard: RepoDashboard, console: Console) -> None:
    """Print the full terminal dashboard, section by section, to `console`."""
    _print_header(dashboard, console)
    _print_languages(dashboard, console)
    _print_files(dashboard, console)
    _print_symbols(dashboard, console)
    _print_relationships(dashboard, console)
    _print_graph(dashboard, console)
    _print_largest_modules(dashboard, console)
    _print_most_connected(dashboard, console)
    _print_health(dashboard, console)


def _stat_table() -> Table:
    """A borderless, headerless two-column table: label left, value right
    — mirrors `quor gain`'s identical `_stat_table()` helper."""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("label")
    table.add_column("value", justify="right")
    return table


def _listing_table(*columns: str) -> Table:
    """A borderless table with visible column headers, for the ranked
    listings (largest modules / most connected files)."""
    table = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    for col in columns:
        table.add_column(col, justify="right" if col != columns[0] else "left")
    return table


def _print_header(d: RepoDashboard, console: Console) -> None:
    console.print("[bold]Quor Repository Dashboard[/bold]")
    console.print()
    console.print(f"Repository: [bold]{d.name}[/bold]")
    console.print(f"Root:       {d.root}")
    commit_suffix = f" (commit {d.git_head[:12]})" if d.git_head else ""
    console.print(f"Indexed:    {format_duration(d.cache_age_seconds)} ago{commit_suffix}")
    console.print()
    console.rule(style="dim")
    console.print()


def _print_languages(d: RepoDashboard, console: Console) -> None:
    if not d.languages:
        return
    console.print("[bold]Languages[/bold]")
    table = _stat_table()
    table.add_column("pct", justify="right")
    for lang in d.languages:
        noun = "file" if lang.file_count == 1 else "files"
        table.add_row(
            lang.language,
            f"[cyan]{format_count(lang.file_count)}[/cyan] {noun}",
            f"[dim]({lang.percentage:.0f}%)[/dim]",
        )
    console.print(table)
    console.print()


def _print_files(d: RepoDashboard, console: Console) -> None:
    console.print("[bold]Files[/bold]")
    table = _stat_table()
    table.add_row("Total files", f"[cyan]{format_count(d.total_files)}[/cyan]")
    table.add_row("Total directories", f"[cyan]{format_count(d.total_directories)}[/cyan]")
    table.add_row("Primary language", f"[cyan]{d.primary_language or 'unknown'}[/cyan]")
    console.print(table)
    console.print()


def _print_symbols(d: RepoDashboard, console: Console) -> None:
    console.print("[bold]Symbols[/bold]")
    table = _stat_table()
    table.add_row("Total symbols", f"[cyan]{format_count(d.total_symbols)}[/cyan]")
    for language, count in sorted(d.symbols_by_language.items(), key=lambda kv: (-kv[1], kv[0])):
        table.add_row(f"  {language}", f"[cyan]{format_count(count)}[/cyan]")
    console.print(table)
    console.print()


def _print_relationships(d: RepoDashboard, console: Console) -> None:
    if not d.relationship_counts:
        return
    console.print("[bold]Relationships[/bold]")
    table = _stat_table()
    for kind, count in sorted(d.relationship_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        table.add_row(kind, f"[cyan]{format_count(count)}[/cyan]")
    console.print(table)
    console.print()


def _print_graph(d: RepoDashboard, console: Console) -> None:
    console.print("[bold]Dependency Graph[/bold]")
    table = _stat_table()
    table.add_row("Nodes", f"[cyan]{format_count(d.graph_nodes)}[/cyan]")
    table.add_row("Edges", f"[cyan]{format_count(d.graph_edges)}[/cyan]")
    resolved_fraction = d.graph_resolved_edges / d.graph_edges if d.graph_edges else 0.0
    table.add_row(
        "Resolved",
        f"[cyan]{format_count(d.graph_resolved_edges)}[/cyan] "
        f"[dim]({format_percentage(resolved_fraction)})[/dim]",
    )
    console.print(table)
    console.print()


def _print_largest_modules(d: RepoDashboard, console: Console) -> None:
    if not d.largest_modules:
        return
    console.print("[bold]Largest modules[/bold]")
    table = _listing_table("path", "language", "symbols")
    for m in d.largest_modules:
        table.add_row(m.path, m.language, format_count(m.symbol_count))
    console.print(table)
    console.print()


def _print_most_connected(d: RepoDashboard, console: Console) -> None:
    if not d.most_connected_files:
        return
    console.print("[bold]Most connected files[/bold]")
    table = _listing_table("path", "out", "in", "total")
    for f in d.most_connected_files:
        table.add_row(f.path, format_count(f.outgoing), format_count(f.incoming), format_count(f.total))
    console.print(table)
    console.print()


def _print_health(d: RepoDashboard, console: Console) -> None:
    findings: list[str] = []
    if d.symbol_parse_failures:
        plural = "file" if d.symbol_parse_failures == 1 else "files"
        findings.append(f"{d.symbol_parse_failures} {plural} failed to parse for symbols at last index.")
    if d.graph_parse_failures:
        plural = "file" if d.graph_parse_failures == 1 else "files"
        findings.append(
            f"{d.graph_parse_failures} {plural} failed to parse for the dependency graph at last index."
        )
    if d.graph_edges:
        unresolved = d.graph_edges - d.graph_resolved_edges
        resolved_fraction = d.graph_resolved_edges / d.graph_edges
        findings.append(
            f"{format_percentage(resolved_fraction)} of dependency edges resolved "
            f"({format_count(unresolved)} unresolved — external/dynamic/ambiguous, by design)."
        )
    findings.extend(d.profile_notes)

    if not findings:
        return
    console.print("[bold]Repository Health[/bold]")
    for line in findings:
        console.print(f"  - {line}")
    console.print()
