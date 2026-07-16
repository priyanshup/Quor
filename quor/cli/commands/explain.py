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

from quor.analytics.compression_summary import build_compression_summary
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
    # track_tokens=True (QB-039) makes the engine record each stage's own
    # before/after token count as it runs, in this same single pipeline
    # pass — the compression summary below is built entirely from that,
    # with no second pipeline execution and no extra render()/count_tokens()
    # calls (see quor.analytics.compression_summary's module docstring).
    result = registry.trace(filter_config, captured, content_type=content_type, track_tokens=True)

    table = Table(title="Stage Trace")
    table.add_column("Stage")
    table.add_column("Lines before", justify="right")
    table.add_column("Lines compressed", justify="right")
    table.add_column("Status")
    for r in result.stage_results:
        status = f"skipped — {r.skip_reason}" if r.was_skipped else "ok"
        table.add_row(r.stage_type, str(r.lines_before), str(r.lines_compressed), status)
    console.print(table)

    original_tokens = count_tokens(captured)
    summary = build_compression_summary(original_tokens, result.stage_results)

    lines = [f"[bold]Tokens:[/bold] Original: {summary.original_tokens:,} tokens (±20% — char/4 approximation)"]
    if summary.lines:
        lines.append("")
        lines.append("Compression summary")
        for line in summary.lines:
            lines.append(f"  - {_humanize_stage(line.stage_type)}: {line.tokens_saved:,} tokens")
    lines.append("")
    lines.append(f"Final: {summary.final_tokens:,} tokens")
    lines.append(f"Saved: {summary.total_saved:,} tokens ({summary.saved_pct:.1f}%)")
    console.print("\n".join(lines))


def _humanize_stage(stage_type: str) -> str:
    """`"group_repeated"` -> `"Group repeated"` — cosmetic only, derived
    purely from the existing `stage_type` identifier (no new per-filter
    metadata is introduced to label these lines)."""
    return stage_type.replace("_", " ").capitalize()
