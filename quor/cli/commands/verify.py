"""quor verify — run every filter's inline [[filter.tests]] entries."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from quor.errors import ExitCode
from quor.filters.registry import FilterRegistry, TestRunResult
from quor.pipeline.ast_summarize.registry import extra_for_language

console = Console()

# Minimum width of the dot-leader column, so a handful of short filter names
# don't produce a cramped, barely-there leader — matches classic Make/test
# runner "name ....... value" alignment.
_NAME_COLUMN_MIN = 20


def verify() -> None:
    """Run all inline filter tests across the three registry tiers. Exit 1 on any failure."""
    registry = FilterRegistry(project_root=Path.cwd())

    rows: list[tuple[str, TestRunResult, int]] = []
    filters_without_tests: list[str] = []
    skipped_languages: set[str] = set()

    for _tier, filter_config in registry.all_filters():
        if not filter_config.tests:
            filters_without_tests.append(filter_config.name)
            continue

        result = registry.run_tests(filter_config)
        rows.append((filter_config.name, result, len(filter_config.tests)))
        if result.skipped:
            skipped_languages.update(
                t.requires_language for t in filter_config.tests if t.requires_language
            )

    console.print("[bold]Built-in filters[/bold]")
    console.print()

    name_width = max((len(name) for name, _, _ in rows), default=0)
    name_width = max(name_width, _NAME_COLUMN_MIN)

    total_tests = 0
    total_failures = 0
    total_skipped = 0

    for name, result, total in rows:
        ran = total - len(result.skipped)
        total_tests += ran
        total_failures += len(result.failures)
        total_skipped += len(result.skipped)

        dots = "." * (name_width - len(name) + 3)

        if result.failures:
            console.print(f"[red]✗[/red] {escape(name)}")
            for f in result.failures:
                console.print(f"    {escape(f)}")
        elif result.skipped:
            console.print(
                f"[yellow]⊘[/yellow] {escape(name)} {dots} skipped "
                "(optional dependency not installed)"
            )
        else:
            console.print(f"[green]✓[/green] {escape(name)} {dots} {ran}/{total}")

    if filters_without_tests:
        console.print()
        console.print(
            "[yellow]Warning: filter(s) with no inline tests: "
            f"{escape(', '.join(filters_without_tests))}[/yellow]"
        )

    skip_note = f", {total_skipped} skipped" if total_skipped else ""
    console.print(f"\n{total_tests} test(s) run{skip_note}, {total_failures} failure(s).")

    if skipped_languages:
        extras = sorted(
            {extra for lang in skipped_languages if (extra := extra_for_language(lang))}
        )
        if extras:
            console.print()
            console.print("Install optional language support:")
            for extra in extras:
                # markup=False: the literal "[javascript]" here is the whole
                # point of the line, not a Rich style tag to be parsed away.
                console.print(f'    pip install "quor[{extra}]"', markup=False)

    if total_failures:
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)
