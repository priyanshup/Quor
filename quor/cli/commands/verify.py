"""quor verify — run every filter's inline [[filter.tests]] entries."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from quor.errors import ExitCode
from quor.filters.registry import FilterRegistry

console = Console()


def verify() -> None:
    """Run all inline filter tests across the three registry tiers. Exit 1 on any failure."""
    registry = FilterRegistry(project_root=Path.cwd())

    total_tests = 0
    total_failures = 0
    filters_without_tests: list[str] = []

    for tier, filter_config in registry.all_filters():
        if not filter_config.tests:
            filters_without_tests.append(f"{filter_config.name} ({tier})")
            continue

        failures = registry.run_tests(filter_config)
        total_tests += len(filter_config.tests)
        total_failures += len(failures)

        if failures:
            console.print(f"[red]✗ {filter_config.name} ({tier})[/red]")
            for f in failures:
                console.print(f"    {f}")
        else:
            console.print(
                f"[green]✓ {filter_config.name} ({tier})[/green] — "
                f"{len(filter_config.tests)} test(s) passed"
            )

    if filters_without_tests:
        console.print(
            f"[yellow]Warning: filter(s) with no inline tests: "
            f"{', '.join(filters_without_tests)}[/yellow]"
        )

    console.print(f"\n{total_tests} test(s) run, {total_failures} failure(s).")

    if total_failures:
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)
