"""quor gain — token savings summary for a project, read from SQLite."""

from __future__ import annotations

from pathlib import Path

import platformdirs
import typer
from rich.console import Console
from rich.table import Table

from quor.config.loader import load_user_config
from quor.tracking.db import query_gain

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

    console.print(f"[bold]Quor gain[/bold] — last {report.days} day(s)")
    console.print(f"Project: {project_path}")
    console.print(f"Mode: {mode}")

    if report.total_invocations == 0:
        console.print(
            f"[yellow]No invocations recorded for this project in the last {days} day(s).[/yellow]"
        )
        raise typer.Exit()

    console.print(f"Total invocations: {report.total_invocations}")
    console.print(f"Tokens saved: ~{report.tokens_saved} (±20% — char/4 approximation)")
    console.print(f"Filter hit rate: {report.filter_hit_rate:.0%}")
    console.print(f"Passthrough count: {report.passthrough_count}")

    if report.top_filters:
        table = Table(title="Top filters by tokens saved")
        table.add_column("Filter")
        table.add_column("Tokens saved", justify="right")
        for name, saved in report.top_filters:
            table.add_row(name, str(saved))
        console.print(table)
