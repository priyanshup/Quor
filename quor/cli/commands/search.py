"""`quor search` — Semantic Repository Search (QB-080).

A 9th exempted utility command, following the exact precedent `quor map`/
`quor symbols`/`quor graph`/`quor repo`/`quor explore` already established:
non-filtering, non-compression, deterministic repository reporting.

**Like `quor explore` (QB-078), this command never calls
`ensure_repo_intelligence()`.** Its sole read path is
`search.load_cache()`, which reads exactly one file — `file_intelligence.json`
— and never `symbol_facts.json`/`graph_facts.json`, never a repository
walk. See `quor/pipeline/repo_profile/search.py`'s own module docstring for
why: those two heavier caches were measured at 114ms combined for this
repository, an O(repo-size) cost this command's own 100ms target can't
absorb.

A single command (not a sub-app like `explore` — `search` has one verb):

    quor search <query> [--kind K] [--language L] [--importance I]
                         [--entry-points] [--limit N] [--path P] [--json]
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import NoReturn, cast, get_args

import orjson
import typer

from quor.cli.repo_path import resolve_repo_root
from quor.errors import ExitCode
from quor.pipeline.repo_profile import search
from quor.pipeline.repo_profile.explorer_model import CacheUnavailable
from quor.pipeline.repo_profile.explorer_render import (
    render_cache_unavailable_json,
    render_cache_unavailable_text,
)
from quor.pipeline.repo_profile.intel_model import FileImportance, FileIntelligenceEntry, FileKind
from quor.pipeline.repo_profile.search_render import render_search_json, render_search_text
from quor.tracking.db import REPO_SEARCH_FILTER_LABEL, get_tracking_db, track_invocation_safe

_FILE_KINDS: tuple[str, ...] = get_args(FileKind)
_FILE_IMPORTANCES: tuple[str, ...] = get_args(FileImportance)

_PATH_OPTION = typer.Option(
    None, "--path", help="Repository root to search (default: current directory)."
)
_JSON_OPTION = typer.Option(False, "--json", help="Output as JSON instead of plain text.")


def _load_cache_or_exit(root: Path, *, json_output: bool) -> dict[str, FileIntelligenceEntry]:
    result = search.load_cache(root)
    if isinstance(result, CacheUnavailable):
        if json_output:
            typer.echo(render_cache_unavailable_json(result))
        else:
            typer.secho(render_cache_unavailable_text(result), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)
    return result


def _exit_invalid_choice(
    option: str, value: str, choices: tuple[str, ...], *, json_output: bool
) -> NoReturn:
    message = f'Invalid {option} "{value}" — must be one of: {", ".join(choices)}.'
    if json_output:
        typer.echo(orjson.dumps({"error": "invalid_option", "message": message}).decode())
    else:
        typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def _exit_empty_query(*, json_output: bool) -> NoReturn:
    message = "Search query must not be empty."
    if json_output:
        typer.echo(orjson.dumps({"error": "empty_query", "message": message}).decode())
    else:
        typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def _exit_invalid_limit(limit: int, *, json_output: bool) -> NoReturn:
    message = f"Invalid --limit {limit} — must be a positive integer."
    if json_output:
        typer.echo(orjson.dumps({"error": "invalid_limit", "message": message}).decode())
    else:
        typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def search_command(
    query: str = typer.Argument(
        ..., help="Search term — matched against symbols, filenames, directories, and dependencies."
    ),
    kind: str | None = typer.Option(
        None, "--kind", help="Filter to one file kind: source, test, generated, configuration."
    ),
    language: str | None = typer.Option(
        None, "--language", help="Filter to one language (case-insensitive)."
    ),
    importance: str | None = typer.Option(
        None, "--importance", help="Filter to one importance tier: high, medium, low."
    ),
    entry_points: bool = typer.Option(
        False, "--entry-points", help="Only show files containing an entry point."
    ),
    limit: int = typer.Option(20, "--limit", help="Maximum number of results to show."),
    path: Path | None = _PATH_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Discover files relevant to a query from cached repository intelligence."""
    t0 = time.monotonic()
    root = resolve_repo_root(path)

    query = query.strip()
    if not query:
        _exit_empty_query(json_output=json_output)

    if limit < 1:
        _exit_invalid_limit(limit, json_output=json_output)

    validated_kind: FileKind | None = None
    if kind is not None:
        kind_casefold = kind.casefold()
        if kind_casefold not in _FILE_KINDS:
            _exit_invalid_choice("--kind", kind, _FILE_KINDS, json_output=json_output)
        validated_kind = cast(FileKind, kind_casefold)

    validated_importance: FileImportance | None = None
    if importance is not None:
        titled = importance.capitalize()
        if titled not in _FILE_IMPORTANCES:
            _exit_invalid_choice(
                "--importance",
                importance,
                tuple(choice.lower() for choice in _FILE_IMPORTANCES),
                json_output=json_output,
            )
        validated_importance = cast(FileImportance, titled)

    entries = _load_cache_or_exit(root, json_output=json_output)

    result = search.search(
        entries,
        query,
        kind=validated_kind,
        language=language,
        importance=validated_importance,
        entry_points_only=entry_points,
        limit=limit,
    )

    output = render_search_json(result) if json_output else render_search_text(result)
    typer.echo(output)
    # Synthesis, not compression — see quor/tracking/db.py's
    # track_invocation_safe() docstring for why original/filtered default
    # equal and this call is fail-open (QB-107).
    track_invocation_safe(
        get_tracking_db,
        command=f"Search: {root.as_posix()}",
        original=output,
        filter_name=REPO_SEARCH_FILTER_LABEL,
        t0=t0,
        close_after=True,
    )
