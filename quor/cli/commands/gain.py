"""quor gain — token savings summary for a project, read from SQLite.

Presentation only: all numbers come straight from GainReport (quor/tracking/
db.py). This module never computes a metric — it only formats and lays out
ones that already exist. See quor/cli/format_utils.py for the (also
presentation-only) number/percentage formatting helpers.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import typer
from rich.console import Console
from rich.table import Table

from quor.cli.format_utils import format_count, format_percentage
from quor.config.loader import load_user_config
from quor.tracking.db import GainReport, query_gain

console = Console()


def gain(
    days: int = typer.Option(30, "--days", help="Number of days to include in the summary."),
    project: Path | None = typer.Option(
        None,
        "--project",
        help="Project path to scope the summary to (default: current directory).",
    ),
) -> None:
    """Show token savings for a project over the last N days."""
    project_path = (project or Path.cwd()).resolve()
    db_path = Path(platformdirs.user_data_dir("quor")) / "quor.db"

    report = query_gain(db_path, project_path, days=days)
    mode = load_user_config().mode

    _print_header(report, project_path=project_path, mode=mode)

    if report.total_invocations == 0:
        console.print()
        console.print(
            f"[yellow]No invocations recorded for this project in the last {days} day(s).[/yellow]"
        )
        raise typer.Exit()

    _print_body(report)


def _print_header(report: GainReport, *, project_path: Path, mode: str) -> None:
    console.print(f"[bold]Quor Gain[/bold] (Last {report.days} days)")
    console.print()
    console.print(f"Project: {project_path}")
    console.print(f"Mode: {mode}")


def _stat_table() -> Table:
    """A borderless, headerless two-column table: label left, value right."""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("label")
    table.add_column("value", justify="right")
    return table


def _print_body(report: GainReport) -> None:
    console.print()
    console.rule(style="dim")

    usage = _stat_table()
    usage.add_row("Commands processed", format_count(report.total_invocations))
    usage.add_row("Filter hit rate", format_percentage(report.filter_hit_rate))
    usage.add_row("Passthrough", format_count(report.passthrough_count))
    console.print(usage)
    console.print()

    tokens = _stat_table()
    tokens.add_row("Tokens before", f"~{format_count(report.tokens_before)}")
    tokens.add_row("Tokens after", f"~{format_count(report.tokens_after)}")
    console.print(tokens)
    console.print()

    saved_fraction = (
        report.tokens_saved / report.tokens_before if report.tokens_before else 0.0
    )
    if report.tokens_saved < 0:
        # Small, already-clean output can legitimately net negative: the tee
        # recovery footer (a fixed ~33-token cost) can exceed genuine
        # compression savings when there's very little to compress (QB-017).
        # Not a bug — but bold green "YOU SAVED -12 tokens" reads as one, so
        # this is styled and worded as a net figure instead of a win.
        console.print("[bold]NET TOKENS[/bold]")
        console.print(
            f"[bold yellow]~{format_count(report.tokens_saved)} tokens "
            f"({format_percentage(saved_fraction)})[/bold yellow]"
        )
        console.print(
            "[dim]A negative net is possible on already-small, already-clean output — "
            "the recovery footer that lets you retrieve the full original can cost more "
            "than compression saved. This does not mean compression failed.[/dim]"
        )
    else:
        console.print("[bold]YOU SAVED[/bold]")
        console.print(
            f"[bold green]~{format_count(report.tokens_saved)} tokens "
            f"({format_percentage(saved_fraction)})[/bold green]"
        )
    console.print()
    console.rule(style="dim")

    visible_filters = [(name, saved) for name, saved in report.top_filters if saved > 0]
    if visible_filters:
        console.print()
        console.print("[bold]Top savings[/bold]")
        console.print()
        filters_table = _stat_table()
        filters_table.add_column("pct", justify="right")
        for name, saved in visible_filters:
            filter_fraction = saved / report.tokens_saved if report.tokens_saved else 0.0
            filters_table.add_row(name, format_count(saved), f"({format_percentage(filter_fraction)})")
        console.print(filters_table)
        console.print()
        console.rule(style="dim")

    console.print()
    console.print("[dim]* Token estimates use the char/4 approximation (±20%), not a real tokenizer.[/dim]")
