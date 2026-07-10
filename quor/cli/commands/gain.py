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
    console.rule(style="dim")

    _print_savings(report)

    visible_filters = [(name, saved) for name, saved in report.top_filters if saved > 0]
    if visible_filters:
        console.print()
        console.print("[bold]Top savings[/bold]")
        console.print()
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
        console.rule(style="dim")

    _print_read_hook_notice(report)

    console.print()
    console.print(
        "[dim]* Each token count (before/after) is estimated via the char/4 "
        "approximation, accurate to roughly ±20% versus a real tokenizer — "
        "the compression percentage itself is not separately uncertain by "
        "±20%, since it compares two estimates computed the same way.[/dim]"
    )


def _print_savings(report: GainReport) -> None:
    """Print the net-savings headline, and — only when at least one
    invocation actually had a negative net this window — the compression
    breakdown and plain-language explanation of why (QB-017).

    Kept conditional rather than always-on: when every invocation genuinely
    shrank (the common case), the breakdown has nothing informative to add
    over the plain net figure, and showing it anyway would be clutter, not
    clarity — the redesign's point is to explain the *exception*, not to
    permanently complicate the common case.
    """
    if report.negative_row_count > 0:
        console.print()
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

    if report.negative_row_count > 0:
        console.print()
        # soft_wrap: this is one continuous explanatory sentence, not a
        # table cell — letting Rich's default word-wrap insert a line break
        # mid-sentence at whatever the terminal width happens to be would
        # both look worse and make plain substring matches on the text
        # (tests, `quor gain | grep`) fragile against terminal width.
        console.print(_negative_row_explainer(report), soft_wrap=True)

    console.print()
    console.rule(style="dim")


def _negative_row_explainer(report: GainReport) -> str:
    """Plain-language answer to "why did some rows get bigger, and should I
    care?" — only ever shown when report.negative_row_count > 0.

    Always explains the mechanism (recovery footer, not a compression
    failure). The closing sentence changes based on whether it's worth
    doing anything about: reassurance when the overall net is still
    positive (the overwhelmingly common case), a concrete, real lever
    (per-filter `tee = false`, an existing FilterConfig option — see
    ADR-023) only when the net for this window is actually negative.
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
    text = (
        f"[dim]{format_count(report.negative_row_count)} of {format_count(report.total_invocations)} "
        f"command{plural} ({format_percentage(row_fraction)}) had output grow instead of shrink. "
        "This is expected, not a bug: it's almost always the recovery footer Quor appends "
        "so the original output stays retrievable (ADR-023), which can cost more than a "
        "small, already-clean command had to compress in the first place. "
        "This does not mean compression failed"
    )
    if report.tokens_saved >= 0:
        text += " — the total above is already net-positive, and it doesn't affect the other commands.[/dim]"
    else:
        text += (
            " — if it matters for a specific noisy command, you can turn off its recovery "
            "footer with `tee = false` in that filter's config.[/dim]"
        )
    return text


def _print_read_hook_notice(report: GainReport) -> None:
    """Only ever printed when `report.read_hook_invocations == 0` — i.e. no
    row in this project/window came from the PostToolUse/Read hook at all.

    Every number above is real and accurate for whatever *did* run, but a
    reader has no way to tell, from the numbers alone, that an entire class
    of filters (Markdown/plain-text compression, DOCX/PDF extraction, and
    AST summarization for files opened via Read rather than `cat`) simply
    never had a chance to contribute — silence here would look identical to
    "these features don't help much," which is not what a `0` means.
    """
    if report.read_hook_invocations > 0:
        return
    console.print()
    console.print(
        "[dim]No Read-hook activity has been recorded in this window, so "
        "features that only run through Claude Code's Read hook — "
        "Markdown/plain-text compression, DOCX/PDF extraction, and AST "
        "summarization for files opened via Read rather than `cat` — are "
        "not represented in the numbers above. Install the Read hook with "
        "`quor init --claude`, then re-check after Claude Code has read "
        "some files.[/dim]",
        soft_wrap=True,
    )
    console.print()
    console.rule(style="dim")
