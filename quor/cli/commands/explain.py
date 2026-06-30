"""quor explain <command> — stage-by-stage trace of how a command would be filtered.

Runs the real command (the user typed it; this carries the same risk as
running it directly in a shell), then shows the classification decision,
which filter tier matched, and what each pipeline stage did to the output.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from quor.filters.registry import FilterRegistry
from quor.pipeline.content_type import detect
from quor.rewrite.classifier import classify_command
from quor.tracking.db import count_tokens

console = Console()


def explain(
    command: str = typer.Argument(..., help="The shell command to explain, e.g. 'git status'."),
) -> None:
    """Show how Quor would classify, filter, and compress a given command."""
    classification = classify_command(command)

    console.print(
        Panel(
            f"[bold]Rewrite:[/bold] {'yes' if classification.should_rewrite else 'no'}\n"
            f"[bold]Reason:[/bold] {classification.reason}\n"
            f"[bold]Rule matched:[/bold] {classification.rule_matched or '-'}\n"
            f"[bold]Rewritten command:[/bold] {classification.rewritten or '(unchanged)'}",
            title="Command Classification",
        )
    )

    registry = FilterRegistry(project_root=Path.cwd())
    filter_config = registry.find(command)

    if filter_config is None:
        console.print("[yellow]No filter matches this command — output passes through unfiltered.[/yellow]")
        raise typer.Exit()

    tier = next((t for t, f in registry.all_filters() if f is filter_config), "unknown")
    console.print(f"[bold]Filter:[/bold] {filter_config.name} (tier: {tier})")

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        console.print(f"[red]Could not run command: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    captured = proc.stdout or ""
    content_type = detect(captured).value
    result = registry.trace(filter_config, captured, content_type=content_type)

    table = Table(title="Stage Trace")
    table.add_column("Stage")
    table.add_column("Lines before", justify="right")
    table.add_column("Lines compressed", justify="right")
    table.add_column("Status")
    for r in result.stage_results:
        status = f"skipped — {r.skip_reason}" if r.was_skipped else "ok"
        table.add_row(r.stage_type, str(r.lines_before), str(r.lines_compressed), status)
    console.print(table)

    rendered = registry.apply(filter_config, captured, content_type=content_type)
    original_tokens = count_tokens(captured)
    final_tokens = count_tokens(rendered)
    console.print(
        f"[bold]Tokens:[/bold] {original_tokens} → {final_tokens} (±20% — char/4 approximation)"
    )
