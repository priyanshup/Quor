"""quor gain — token savings summary for a project, read from SQLite.

Presentation only: all numbers come straight from GainReport (quor/tracking/
db.py). This module never computes a metric — it only formats and lays out
ones that already exist. See quor/cli/format_utils.py for the (also
presentation-only) number/percentage formatting helpers.

Layout (scannability pass): the headline (net tokens saved) leads, backed
by one compact stat table and Top savings — compression is the whole
point of the tool and gets top billing. Two kinds of caveat are handled
differently on purpose:
  - Read-hook coverage gap affects how *every* number above should be
    read (an entire class of filters had no chance to run), so it prints
    once, up front, before any statistic.
  - Recovery-footer overhead is a narrow footnote about a subset of rows,
    not a qualifier on the headline — it prints after the stats, dimmed,
    same tier as the token-estimation note.
`console.highlight` is off: Rich's automatic number highlighter matches
digits with a `\\b` word boundary, which does not exist between a digit
and a following letter — so "81.1k" highlights as "81." colored + "1k"
uncolored, a visibly broken split. Every number below is colored, if at
all, by an explicit markup span we control, wrapping the whole formatted
string in one style, never by that highlighter.
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

console = Console(highlight=False)


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
    console.print()

    if report.read_hook_invocations == 0:
        console.print(_read_hook_notice_text(), soft_wrap=True)
        console.print()

    _print_headline(report)
    _print_stats(report)
    _print_top_savings(report)

    if report.negative_row_count > 0:
        console.print(_negative_row_notice_text(report), soft_wrap=True)
        console.print()

    console.rule(style="dim")
    console.print()
    _print_notes()


def _read_hook_notice_text() -> str:
    """Shown whenever `report.read_hook_invocations == 0` — i.e. no row in
    this project/window came from the PostToolUse/Read hook at all. Every
    number in the report is real and accurate for whatever *did* run, but a
    reader has no way to tell, from the numbers alone, that an entire class
    of filters (Markdown/plain-text compression, DOCX/PDF extraction, and
    AST summarization for files opened via Read rather than `cat`) simply
    never had a chance to contribute — silence here would look identical to
    "these features don't help much," which is not what a `0` means. One
    line, not a paragraph: this is a scope caveat, not an essay.
    """
    return (
        "[yellow]No Read-hook activity has been recorded in this window — "
        "Markdown/plain-text compression, DOCX/PDF extraction, and AST "
        "summarization aren't represented above. Run `quor init --claude` "
        "to enable.[/yellow]"
    )


def _negative_row_notice_text(report: GainReport) -> str:
    """A compact, dimmed footnote — not an explainer paragraph — for rows
    where the recovery footer pushed output above its original size.
    Always names the actual lever: reassurance when the window's overall
    net is still positive, the real per-filter opt-out (`tee = false`)
    when it isn't (QB-017)."""
    row_fraction = (
        report.negative_row_count / report.total_invocations
        if report.total_invocations
        else 0.0
    )
    plural = "s" if report.negative_row_count != 1 else ""
    lever = (
        "doesn't affect the other commands."
        if report.tokens_saved >= 0
        else "turn it off per-filter with `tee = false` if it matters."
    )
    return (
        f"[dim]Recovery footer   {format_count(report.negative_row_count)} "
        f"command{plural} ({format_percentage(row_fraction)}) · {lever}[/dim]"
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
        breakdown.add_row(
            "Compression achieved", f"[cyan]~{format_count(report.gross_savings)} tokens[/cyan]"
        )
        breakdown.add_row(
            "Recovery-footer overhead",
            f"[dim]~{format_count(report.gross_overhead)} tokens[/dim]",
        )
        console.print(breakdown)
        console.print()

    saved_fraction = (
        report.tokens_saved / report.tokens_before if report.tokens_before else 0.0
    )
    if report.tokens_saved < 0:
        # Bold "YOU SAVED -12 tokens" would read as a bug even though it
        # isn't one — styled and worded as a net figure instead of a win.
        console.print(
            f"[bold]NET TOKENS[/bold]   [bold yellow]~{format_count(report.tokens_saved)} "
            f"tokens ({format_percentage(saved_fraction)})[/bold yellow]"
        )
    else:
        console.print(
            f"[bold]YOU SAVED[/bold]   [bold green]~{format_count(report.tokens_saved)} "
            f"tokens ({format_percentage(saved_fraction)})[/bold green]"
        )
    console.print()


def _print_stats(report: GainReport) -> None:
    """Every secondary number in one compact table instead of three stacked
    ones — faster to scan, same figures. Values share one consistent
    style (bold cyan) so the eye reads "this column is numbers" at a
    glance; only the headline above earns the celebratory green/yellow."""
    stats = _stat_table()
    stats.add_row("Commands processed", f"[cyan]{format_count(report.total_invocations)}[/cyan]")
    stats.add_row("Filter hit rate", f"[cyan]{format_percentage(report.filter_hit_rate)}[/cyan]")
    stats.add_row("Passthrough", f"[cyan]{format_count(report.passthrough_count)}[/cyan]")
    stats.add_row("Tokens before", f"[cyan]~{format_count(report.tokens_before)}[/cyan]")
    stats.add_row("Tokens after", f"[cyan]~{format_count(report.tokens_after)}[/cyan]")
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
        filters_table.add_row(
            name,
            f"[cyan]{format_count(saved)}[/cyan]",
            f"[dim]({format_percentage(filter_fraction)})[/dim]",
        )
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
