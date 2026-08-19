"""quor discover — QB-034: retroactively show what Quor would have saved on
past Claude Code sessions for this project, before it was adopted (or on
commands it never had a chance to see).

Presentation only: all scanning/scoring logic lives in
`quor.discovery.session_scan`, exactly the same "engine vs. presentation"
split `gain.py`/`gain_presentation.py` already establish for `quor gain`.

Never displays raw command text or raw command output — only Claude's own
short, human-written `description` for each `Bash` call (safer than echoing
a command that might embed a credential) and aggregate/per-filter numbers.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from quor.cli.format_utils import format_count, format_percentage
from quor.discovery.session_scan import DiscoverReport, scan_project

console = Console(highlight=False)


def discover(
    days: int = typer.Option(
        30, "--days", help="Number of days of past sessions to scan."
    ),
    project: Path | None = typer.Option(
        None,
        "--project",
        help="Project path to scan sessions for (default: current directory).",
    ),
    top: int = typer.Option(
        10, "--top", help="Number of highest-savings commands to list individually."
    ),
) -> None:
    """Scan past Claude Code sessions for this project and show what Quor
    would have saved on commands it never compressed."""
    project_path = (project or Path.cwd()).resolve()
    report = scan_project(project_path, days=days, top_n=top)

    console.print(f"[bold]Quor Discover[/bold] (Last {days} days)")
    console.print()
    console.print(f"Project: {project_path}")
    console.print()

    if report.sessions_scanned == 0:
        console.print(
            "[yellow]No Claude Code session transcripts found for this project in the "
            f"last {days} day(s) — nothing to scan.[/yellow]"
        )
        _print_notes()
        return

    console.rule(style="dim")
    console.print()

    if report.commands_scanned == 0:
        console.print(
            f"[yellow]Scanned {format_count(report.sessions_scanned)} session(s), "
            "found no Bash commands to evaluate.[/yellow]"
        )
        _print_notes()
        return

    _print_headline(report)
    _print_stats(report)
    _print_by_filter(report)
    _print_top_commands(report)

    console.rule(style="dim")
    console.print()
    _print_notes()


def _print_headline(report: DiscoverReport) -> None:
    console.print(
        f"[bold]QUOR WOULD HAVE SAVED[/bold]   [bold green]~{format_count(report.total_tokens_would_save)} "
        f"tokens ({format_percentage(report.would_save_pct / 100)})[/bold green]"
    )
    console.print()


def _print_stats(report: DiscoverReport) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("label")
    table.add_column("value", justify="right", style="bold cyan")
    table.add_row("Sessions scanned", format_count(report.sessions_scanned))
    table.add_row("Bash commands scanned", format_count(report.commands_scanned))
    if report.commands_already_covered:
        table.add_row(
            "Already compressed by Quor", format_count(report.commands_already_covered)
        )
    table.add_row("Original tokens", format_count(report.total_original_tokens))
    console.print(table)
    console.print()


def _print_by_filter(report: DiscoverReport) -> None:
    if not report.by_filter:
        return
    table = Table(title="By filter")
    table.add_column("Filter")
    table.add_column("Commands", justify="right")
    table.add_column("Would save", justify="right")
    for name, agg in sorted(
        report.by_filter.items(), key=lambda kv: kv[1].tokens_would_save, reverse=True
    ):
        if agg.tokens_would_save <= 0:
            continue
        table.add_row(name, format_count(agg.count), f"~{format_count(agg.tokens_would_save)}")
    if table.row_count:
        console.print(table)
        console.print()


def _print_top_commands(report: DiscoverReport) -> None:
    if not report.top_commands:
        return
    table = Table(title="Top savings")
    table.add_column("Command")
    table.add_column("Filter")
    table.add_column("Would save", justify="right")
    for cmd in report.top_commands:
        if cmd.tokens_would_save <= 0:
            continue
        table.add_row(
            _truncate(cmd.description, 60),
            cmd.matched_filter or "(none)",
            f"~{format_count(cmd.tokens_would_save)}",
        )
    if table.row_count:
        console.print(table)
        console.print()


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _print_notes() -> None:
    console.print(
        "[dim]· Token counts are estimated via the char/4 approximation, "
        "±20% versus a real tokenizer.[/dim]",
        soft_wrap=True,
    )
    console.print(
        "[dim]· Only Bash command output is scanned (not file reads) — reads "
        "session transcripts under ~/.claude/projects/, nothing is stored "
        "or transmitted.[/dim]",
        soft_wrap=True,
    )
