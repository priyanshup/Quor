"""quor validate [file] — validate filter configuration without executing anything.

Must complete in <1 second. No subprocess execution.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import typer

from quor.errors import ConfigError, ExitCode
from quor.filters.loader import load_filter_file
from quor.filters.registry import FilterRegistry


def validate(
    file: Path | None = typer.Argument(
        None, help="Specific filter TOML file to validate. Omit to validate all three registry tiers."
    ),
) -> None:
    """Validate filter configuration: a single file, or all three registry tiers."""
    if file is not None:
        _validate_single(file)
        return
    _validate_all_tiers()


def _validate_single(file: Path) -> None:
    try:
        filters = load_filter_file(file)
    except ConfigError as exc:
        typer.secho(f"✗ {file}: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=ExitCode.CONFIG_ERROR) from exc

    for f in filters:
        typer.echo(f"✓ {f.name}: {len(f.stages)} stage(s), {len(f.tests)} test(s)")


def _validate_all_tiers() -> None:
    load_warnings: list[str] = []
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        registry = FilterRegistry(project_root=Path.cwd())
        load_warnings = [str(w.message) for w in captured if "[quor]" in str(w.message)]

    for tier, f in registry.all_filters():
        typer.echo(f"✓ [{tier}] {f.name}: {len(f.stages)} stage(s), {len(f.tests)} test(s)")

    if load_warnings:
        typer.echo()
        for msg in load_warnings:
            typer.secho(f"✗ {msg}", fg=typer.colors.RED)
        raise typer.Exit(code=ExitCode.CONFIG_ERROR)
