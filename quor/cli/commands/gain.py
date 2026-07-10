"""quor gain — token savings summary for a project, read from SQLite.

Presentation only: all numbers come straight from GainReport (quor/tracking/
db.py). This module never computes a metric — it only formats and lays out
ones that already exist. See quor/cli/format_utils.py for the (also
presentation-only) number/percentage formatting helpers.

Layout (QB-037 dashboard redesign): notices (Read-hook coverage gaps,
recovery-footer overhead) print in a dedicated NOTICE block before any
statistics, never interleaved with them — the headline savings number leads
the statistics themselves, followed by one compact stat table (instead of
three stacked ones) and Top savings. Long explanatory paragraphs from the
previous layout are now one-line notes. No calculation changed; this only
changes how existing GainReport fields are arranged and worded.
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

    _print_report(report)


def _print_header(report: GainReport, *, project_path: Path, mode: str) -> None:
    console.print(f"[bold]Quor Gain[/bold] (Last {report.days} days)")
    console.print()
    console.print(f"Project: {project_path}")
    # `mode` (quor.config.model.QuorUserConfig.mode) only ever reaches
    # PluginContext for third-party plugins (see dispatcher.py's
    # _setup_plugins) — registry.apply(), the call that actually produces
    # every number below, never reads it. "Mode: audit" sitting directly
    # above compression statistics reads like it qualifies them; it
    # doesn't, for any mode value. Only annotated for the non-default
    # values, since "optimize" (the common case) is exactly what a reader
    # would already assume without a caveat.
    if mode == "optimize":
        console.print(f"Mode: {mode}")
    else:
        console.print(
            f"Mode: {mode} [dim](affects third-party plugins only — "
            "the compression numbers below always reflect real, applied "
            "compression regardless of mode)[/dim]"
        )


def _stat_table() -> Table:
    """A borderless, headerless two-column table: label left, value right."""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("label")
    table.add_column("value", justify="right")
    return table


def _print_report(report: GainReport) -> None:
    console.print()
    console.rule(style="dim")

    _print_notices(report)
    _print_headline(report)
    _print_stats(report)
    _print_top_savings(report)

    console.rule(style="dim")
    console.print()
    _print_notes()


def _print_notices(report: GainReport) -> None:
    """Every informational notice, together, before any statistic —
    deliberately separate from the stats below so a reader never has to
    guess whether a caveat qualifies the number sitting next to it (QB-037).
    Prints nothing at all when there's nothing to say.
    """
    notices: list[str] = []

    if report.read_hook_invocations == 0:
        notices.append(_read_hook_notice_text())
    if report.negative_row_count > 0:
        notices.append(_negative_row_notice_text(report))

    if not notices:
        return

    console.print()
    console.print("[bold yellow]NOTICE[/bold yellow]")
    for i, text in enumerate(notices):
        console.print(text, soft_wrap=True)
        if i < len(notices) - 1:
            console.print()
    console.print()
    console.rule(style="dim")


def _read_hook_notice_text() -> str:
    """Shown whenever `report.read_hook_invocations == 0` — i.e. no row in
    this project/window came from the PostToolUse/Read hook at all. Every
    number in the report is real and accurate for whatever *did* run, but a
    reader has no way to tell, from the numbers alone, that an entire class
    of filters (Markdown/plain-text compression, DOCX/PDF extraction, and
    AST summarization for files opened via Read rather than `cat`) simply
    never had a chance to contribute — silence here would look identical to
    "these features don't help much," which is not what a `0` means.
    """
    return (
        "No Read-hook activity has been recorded in this window.\n"
        "Markdown/plain-text compression, DOCX/PDF extraction, and AST "
        "summarization only run through Claude Code's Read hook and aren't "
        "represented above.\n"
        "Run `quor init --claude` to enable Read tracking."
    )


def _negative_row_notice_text(report: GainReport) -> str:
    """Plain-language answer to "why did some rows get bigger, and should I
    care?" — only ever shown when report.negative_row_count > 0. Always
    names the mechanism (recovery footer, not a compression failure); the
    closing clause offers a concrete lever (`tee = false`) only when the
    net for this window is actually negative, otherwise reassures that the
    overall total is still positive (QB-017).
    """
    row_fraction = (
        report.negative_row_count / report.total_invocations
        if report.total_invocations
        else 0.0
    )
    # Plural agrees with total_invocations (the noun "command(s)" is
    # counting), not negative_row_count — "1 of 2 commands", not
    # "1 of 2 command".
    plural = "s" if report.total_invocations != 1 else ""
    lever = (
        "it doesn't affect the other commands."
        if report.tokens_saved >= 0
        else "turn it off per-filter with `tee = false` if it matters."
    )
    return (
        f"{format_count(report.negative_row_count)} of {format_count(report.total_invocations)} "
        f"command{plural} ({format_percentage(row_fraction)}) had output grow instead of shrink "
        f"— the recovery footer, not a compression failure. This does not mean compression "
        f"failed; {lever}"
    )


def _print_headline(report: GainReport) -> None:
    """The one number that matters most, first: net tokens saved (or, for a
    net-negative window, the plain net figure — never styled as a win it
    wasn't). The gross-savings/overhead breakdown, when shown, sits directly
    above it rather than as a separate section, since it's context for the
    same headline, not a distinct statistic.
    """
    if report.negative_row_count > 0:
        breakdown = _stat_table()
        breakdown.add_row("Compression achieved", f"~{format_count(report.gross_savings)} tokens")
        breakdown.add_row("Recovery-footer overhead", f"~{format_count(report.gross_overhead)} tokens")
        console.print(breakdown)
        console.print()

    saved_fraction = (
        report.tokens_saved / report.tokens_before if report.tokens_before else 0.0
    )
    if report.tokens_saved < 0:
        # Bold green "YOU SAVED -12 tokens" would read as a bug even though
        # it isn't one — styled and worded as a net figure instead of a win.
        console.print("[bold]NET TOKENS[/bold]")
        console.print(
            f"[bold yellow]~{format_count(report.tokens_saved)} tokens "
            f"({format_percentage(saved_fraction)})[/bold yellow]"
        )
    else:
        console.print("[bold]YOU SAVED[/bold]")
        console.print(
            f"[bold green]~{format_count(report.tokens_saved)} tokens "
            f"({format_percentage(saved_fraction)})[/bold green]"
        )
    console.print()


def _print_stats(report: GainReport) -> None:
    """Every secondary number in one compact table instead of three stacked
    ones — faster to scan, same figures."""
    stats = _stat_table()
    stats.add_row("Commands processed", format_count(report.total_invocations))
    stats.add_row("Filter hit rate", format_percentage(report.filter_hit_rate))
    stats.add_row("Passthrough", format_count(report.passthrough_count))
    stats.add_row("Tokens before", f"~{format_count(report.tokens_before)}")
    stats.add_row("Tokens after", f"~{format_count(report.tokens_after)}")
    console.print(stats)
    console.print()


def _print_top_savings(report: GainReport) -> None:
    visible_filters = [(name, saved) for name, saved in report.top_filters if saved > 0]
    if not visible_filters:
        return

    console.print("[bold]Top savings[/bold]")
    filters_table = _stat_table()
    filters_table.add_column("pct", justify="right")
    # Percentage of *genuine compression achieved* (gross_savings), not
    # of the net figure — net can be small or negative even when real
    # per-filter savings are large (QB-017: overhead elsewhere shouldn't
    # make an unrelated filter's own contribution look distorted).
    denominator = report.gross_savings or report.tokens_saved
    for name, saved in visible_filters:
        filter_fraction = saved / denominator if denominator else 0.0
        filters_table.add_row(name, format_count(saved), f"({format_percentage(filter_fraction)})")
    console.print(filters_table)
    console.print()


def _print_notes() -> None:
    """One line, not a paragraph — the ±20% estimation caveat ANTI_GOALS.md
    #24 requires on every token count, kept but no longer expanded into a
    multi-sentence justification of why the percentage itself isn't
    separately uncertain."""
    console.print(
        "[dim]· Token counts are estimated via the char/4 approximation, "
        "±20% versus a real tokenizer.[/dim]",
        soft_wrap=True,
    )
