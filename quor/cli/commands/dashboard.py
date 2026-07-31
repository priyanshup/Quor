"""quor dashboard — live terminal view of token savings (QB-083).

A ninth exempted utility command, same precedent as `quor repo`/`quor map`/
`quor symbols`/`quor graph`/`quor explore`/`quor search` (non-filtering,
presentation-only — reads already-recorded tracking data, computes nothing
new). Unlike those, this is the one *live*, auto-refreshing view in Quor —
deliberately built as a terminal TUI (`rich.live.Live`, already a core
dependency) rather than a browser dashboard: `docs/final/ANTI_GOALS.md` #7
rules out a web UI outright ("no Flask, no FastAPI... the browser is not in
scope"). This mirrors the reference product's own terminal dashboard
(`headroom dashboard`) rather than its separate browser one. Foreground-only,
no port, no persistent daemon (ANTI_GOALS.md #11's "no watch mode" spirit) —
starts when you run it, stops on Ctrl-C, never runs unattended.

Token counts remain the existing char/4 approximation (ADR-013) — a real
tokenizer (`tiktoken`) was considered for this feature and explicitly
declined, to avoid reintroducing the compiled-dependency risk ADR-013/
ANTI_GOALS.md #6 already ruled out. Every number here carries the same
±20% disclaimer `quor gain` already shows; nothing about this command
changes that story.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import platformdirs
import typer
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from quor.cli.format_utils import format_count, format_duration, format_percentage
from quor.cli.gain_presentation import (
    build_stats_table,
    build_top_filters_table,
    eligible_compression_line,
    filter_display_name,
    low_sample_caveat,
)
from quor.tracking.db import GainReport, RecentInvocation, query_gain, query_recent_invocations

console = Console(highlight=False)

_DEFAULT_REFRESH_SECONDS = 1.0
_RECENT_LIMIT = 10

# Reference price only — never a live/queried number. Claude Sonnet input
# token pricing, USD per million tokens, as published by Anthropic at time
# of writing. A single named constant, not a per-model table, since
# count_tokens() has no idea which model produced any given line either —
# the estimate is explicitly labeled as such wherever it's shown.
_REFERENCE_PRICE_PER_MILLION_INPUT_TOKENS = 3.00

_WAITING_MESSAGE = (
    "Waiting for activity — run some commands through Claude Code (or `quor <cmd>` "
    "directly) to see savings here."
)

_FOOTER_NOTE = (
    "[dim]· Token counts are estimated via the char/4 approximation, ±20% versus a "
    "real tokenizer. Estimated cost uses a fixed reference price and is not a "
    "guaranteed figure. This view only covers activity since this dashboard started — "
    "`quor gain` shows a longer (30-day, by default) window.[/dim]"
)


def dashboard_command(
    project: Path | None = typer.Option(
        None,
        "--project",
        help="Project path to scope the dashboard to (default: current directory).",
    ),
    refresh: float = typer.Option(
        _DEFAULT_REFRESH_SECONDS,
        "--refresh",
        min=0.25,
        help="Seconds between refreshes.",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Print a single snapshot and exit, instead of a live-refreshing view.",
    ),
) -> None:
    """Live terminal view of token savings since this command started."""
    project_path = (project or Path.cwd()).resolve()
    db_path = Path(platformdirs.user_data_dir("quor")) / "quor.db"
    session_start = datetime.now(UTC)

    # A piped/non-interactive caller (CI, a script) can't usefully watch a
    # live-refreshing display — fall back to one static snapshot rather than
    # hang. --once requests the same behavior explicitly, e.g. for scripting.
    if once or not console.is_terminal:
        console.print(_render(db_path, project_path, session_start))
        return

    try:
        with Live(console=console, screen=False, auto_refresh=False) as live:
            # QB-091: tracked across ticks purely to draw the ▲/▼ trend
            # marker next to the headline percentage — so a number that
            # visibly changed between two glances at the live view reads as
            # expected motion rather than a surprise. `quor gain` (a single
            # snapshot, no "previous tick" to compare against) has no
            # equivalent of this.
            previous_fraction: float | None = None
            while True:
                report = query_gain(db_path, project_path, since=session_start)
                live.update(
                    _render(
                        db_path,
                        project_path,
                        session_start,
                        previous_fraction=previous_fraction,
                        report=report,
                    ),
                    refresh=True,
                )
                previous_fraction = (
                    report.tokens_saved / report.tokens_before if report.tokens_before else None
                )
                time.sleep(refresh)
    except KeyboardInterrupt:
        pass


def _render(
    db_path: Path,
    project_path: Path,
    session_start: datetime,
    previous_fraction: float | None = None,
    *,
    report: GainReport | None = None,
) -> Group:
    if report is None:
        report = query_gain(db_path, project_path, since=session_start)
    elapsed = (datetime.now(UTC) - session_start).total_seconds()
    header = (
        f"[bold]Quor Dashboard[/bold]   {project_path}   "
        f"[dim]watching for {format_duration(elapsed)}[/dim]"
    )

    if report.total_invocations == 0:
        return Group(header, "", Panel(_WAITING_MESSAGE, border_style="dim"))

    recent = query_recent_invocations(
        db_path, project_path, since=session_start, limit=_RECENT_LIMIT
    )

    parts: list[RenderableType] = [header, "", _headline_text(report, previous_fraction)]
    eligible_line = eligible_compression_line(report)
    if eligible_line is not None:
        parts.append(eligible_line)
    parts += [build_stats_table(report), _cost_line(report), ""]

    top_filters = build_top_filters_table(report)
    if top_filters is not None:
        parts += ["[bold]Top filters this session[/bold]", top_filters, ""]

    recent_table = _recent_table(recent)
    if recent_table is not None:
        parts += ["[bold]Recent activity[/bold]", recent_table, ""]

    parts.append(_FOOTER_NOTE)
    return Group(*parts)


def _trend_marker(current_fraction: float, previous_fraction: float | None) -> str:
    """A tiny live-only indicator showing whether the headline percentage
    moved since the last refresh tick (QB-091) — so a number that visibly
    changes between two glances at the dashboard reads as expected live
    motion, not a surprise. `None`/no previous tick yet renders nothing."""
    if previous_fraction is None:
        return ""
    delta = current_fraction - previous_fraction
    if abs(delta) < 0.005:  # under half a percentage point — treat as flat, avoid jitter
        return " [dim]·[/dim]"
    arrow, color = ("▲", "green") if delta > 0 else ("▼", "yellow")
    return f" [{color}]{arrow}[/{color}]"


def _headline_text(report: GainReport, previous_fraction: float | None = None) -> str:
    saved_fraction = report.tokens_saved / report.tokens_before if report.tokens_before else 0.0
    caveat = low_sample_caveat(report)
    trend = _trend_marker(saved_fraction, previous_fraction) if not caveat else ""
    if report.tokens_saved < 0:
        return (
            f"[bold]NET TOKENS[/bold]   [bold yellow]~{format_count(report.tokens_saved)} "
            f"tokens ({format_percentage(saved_fraction)})[/bold yellow]{trend}{caveat}"
        )
    return (
        f"[bold]SAVED SO FAR[/bold]   [bold green]~{format_count(report.tokens_saved)} "
        f"tokens ({format_percentage(saved_fraction)})[/bold green]{trend}{caveat}"
    )


def _estimated_cost_saved(tokens_saved: int) -> float:
    return max(tokens_saved, 0) / 1_000_000 * _REFERENCE_PRICE_PER_MILLION_INPUT_TOKENS


def _cost_line(report: GainReport) -> str:
    cost = _estimated_cost_saved(report.tokens_saved)
    return (
        f"[dim]Estimated cost saved: ~${cost:,.2f} "
        f"(reference: ${_REFERENCE_PRICE_PER_MILLION_INPUT_TOKENS:.2f}/M input tokens)[/dim]"
    )


def _recent_table(rows: list[RecentInvocation]) -> Table | None:
    if not rows:
        return None
    table = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    table.add_column("Time")
    table.add_column("Command")
    table.add_column("Filter")
    table.add_column("Before → After", justify="right")
    for r in rows:
        time_label = r.recorded_at.split("T")[-1][:8] if "T" in r.recorded_at else r.recorded_at
        table.add_row(
            time_label,
            _truncate(r.command, 40),
            filter_display_name(r.filter_name) if r.filter_name else "[dim](passthrough)[/dim]",
            f"{format_count(r.original_tokens)} → {format_count(r.final_tokens)}",
        )
    return table


def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"
