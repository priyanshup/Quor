"""Presentation logic shared between `quor gain` and `quor dashboard`
(QB-091).

Both commands render the same `GainReport` shape and had drifted out of
sync by hand — `quor dashboard` silently omitted the "Passthrough" stat
`quor gain` already showed, purely because each command kept its own copy
of the same table-building code. Centralizing the shared pieces here means
that class of drift can't recur: fix it once, both commands pick it up.

Also home to the two clarity fixes from QB-091's UX review:
  - `eligible_compression_line()` — a second, narrower compression figure
    for when some commands were "passthrough" (no filter exists for that
    content at all, e.g. `ps`/`grep` output). The headline percentage
    blends eligible and ineligible commands together, which is exactly
    what makes it swing hard as ineligible commands accumulate in a
    session — both the headline and this line are correct, they just
    answer different questions.
  - `low_sample_caveat()` — a small, honest caveat appended once the
    sample is too small (few commands recorded) for the percentage to mean
    much yet, rather than presenting an early, volatile number with the
    same visual confidence as a settled one.

Like quor/cli/format_utils.py, this module is presentation-only: it never
computes a metric, only formats and lays out ones that already exist in a
GainReport.
"""

from __future__ import annotations

from rich.table import Table

from quor.cli.format_utils import format_count, format_percentage
from quor.tracking.db import GainReport

# Below this many recorded commands, the blended percentage is dominated by
# whichever single command happened to run first rather than by anything
# representative — a single large compressible file read can make the
# headline read "87%" on command #1 and "7%" on command #45, with the
# arithmetic correct at every point in between. Rather than hide the
# number, callers append low_sample_caveat()'s text once the sample is
# this small; at or above this count the headline is shown exactly as
# before, with no caveat.
LOW_SAMPLE_THRESHOLD = 5

# Internal filter-registry ids that don't self-explain to a user. Most
# filter names ARE the real tool a developer would recognize — "pytest",
# "eslint", "docker" — and are left untranslated on purpose; this table
# only covers the generic `cat`/`cat-<language>` family (an
# implementation-detail name for "read + AST-aware compress this file
# type") plus a couple of other internal-only ids.
_FILTER_DISPLAY_NAMES: dict[str, str] = {
    "cat": "Text file read",
    "cat-python": "Python file read",
    "cat-javascript": "JavaScript file read",
    "cat-typescript": "TypeScript file read",
    "cat-tsx": "TSX file read",
    "cat-json": "JSON file read",
    "cat-yaml": "YAML file read",
    "cat-toml": "TOML file read",
    "cat-go": "Go file read",
    "cat-java": "Java file read",
    "cat-csharp": "C# file read",
    "cat-rust": "Rust file read",
    "generic": "Command output",
    "document-text": "Document text",
}


def filter_display_name(name: str) -> str:
    """User-facing label for a filter id. Anything not in
    `_FILTER_DISPLAY_NAMES` passes through unchanged — most filter names
    already are the right vocabulary for this audience."""
    return _FILTER_DISPLAY_NAMES.get(name, name)


def low_sample_caveat(report: GainReport) -> str:
    """Trailing, dim caveat for a headline built from too few commands to
    be a stable read yet. Empty once `total_invocations` reaches
    LOW_SAMPLE_THRESHOLD, so callers can unconditionally append it without
    an extra branch."""
    if report.total_invocations >= LOW_SAMPLE_THRESHOLD:
        return ""
    plural = "s" if report.total_invocations != 1 else ""
    return (
        f" [dim]· early read ({report.total_invocations} command{plural}) — "
        "this settles as more commands run[/dim]"
    )


def eligible_compression_line(report: GainReport) -> str | None:
    """A second compression figure scoped to only the commands a filter
    could actually run against — `None` when there's nothing to
    disambiguate (no passthrough commands recorded yet, so it would just
    repeat the headline)."""
    if report.passthrough_count == 0 or report.eligible_before <= 0:
        return None
    fraction = report.tokens_saved / report.eligible_before
    return (
        f"[dim]· On the {format_percentage(report.filter_hit_rate)} of commands a filter "
        f"could apply to: {format_percentage(fraction)} smaller[/dim]"
    )


def build_stats_table(report: GainReport) -> Table:
    """The secondary stats block under the headline — identical between
    `quor gain` and `quor dashboard` by construction."""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("label")
    table.add_column("value", justify="right")
    table.add_row("Commands processed", f"[cyan]{format_count(report.total_invocations)}[/cyan]")
    table.add_row("Filter hit rate", f"[cyan]{format_percentage(report.filter_hit_rate)}[/cyan]")
    table.add_row("Passthrough", f"[cyan]{format_count(report.passthrough_count)}[/cyan]")
    table.add_row("Tokens before", f"[cyan]~{format_count(report.tokens_before)}[/cyan]")
    table.add_row("Tokens after", f"[cyan]~{format_count(report.tokens_after)}[/cyan]")
    return table


def build_top_filters_table(report: GainReport) -> Table | None:
    """The "top savings" table shown by both commands, using
    `filter_display_name()` instead of raw internal filter ids. `None`
    when nothing saved a nonzero number of tokens."""
    visible = [(name, saved) for name, saved in report.top_filters if saved > 0]
    if not visible:
        return None
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column("label")
    table.add_column("value", justify="right")
    table.add_column("pct", justify="right")
    denominator = report.gross_savings or report.tokens_saved
    for name, saved in visible:
        fraction = saved / denominator if denominator else 0.0
        table.add_row(
            filter_display_name(name),
            f"[cyan]{format_count(saved)}[/cyan]",
            f"[dim]({format_percentage(fraction)})[/dim]",
        )
    return table
