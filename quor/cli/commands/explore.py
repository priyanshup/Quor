"""`quor explore` — Repository Explorer (QB-078).

An 8th exempted utility command, following the exact precedent `quor map`/
`quor symbols`/`quor graph`/`quor repo` already established (ADR-037/038/
039 — see `docs/final/DECISIONS.md`'s own ADR-042 for this command):
non-filtering, non-compression, deterministic repository reporting.

**Unlike `quor repo` (QB-077), this command never calls
`ensure_repo_intelligence()`.** QB-078's own spec is explicit: a reporting
command must never walk, parse, or rebuild the repository, and must answer
in well under 100ms using only whatever `quor map`/`quor symbols`/`quor
graph`/`quor repo` last cached. `quor/pipeline/repo_profile/explorer.py`'s
`load_cache()` is the sole read path — see its own module docstring for why
that is a deliberate step back from QB-077's auto-refresh philosophy, not an
oversight. When no cache exists, or it exists but can't be read, this
command's own subcommands report exactly which (`load_cache()` distinguishes
"missing" from "corrupted", each with its own actionable guidance) and exit
non-zero — they never build one themselves, even though `quor map`/`quor
repo` are one command away.

Five subcommands, one Typer sub-app (`explore_app`, registered under
`"explore"` in `quor/cli/main.py`) — `find <name>`, `deps <file>`,
`used-by <file>`, `file <file>`, `stats`. Every subcommand accepts `--path`
(the repository root, resolved via the same shared `resolve_repo_root()`
`quor map`/`quor symbols`/`quor graph`/`quor repo` already use) and `--json`
(stable, schema-per-result-type JSON via `explorer_render.py`).
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path, PurePosixPath
from typing import NoReturn

import orjson
import typer

from quor.cli.repo_path import resolve_repo_root
from quor.errors import ExitCode
from quor.pipeline.repo_profile import explorer
from quor.pipeline.repo_profile.explorer_model import CacheUnavailable
from quor.pipeline.repo_profile.explorer_render import (
    render_cache_unavailable_json,
    render_cache_unavailable_text,
    render_deps_json,
    render_deps_text,
    render_file_json,
    render_file_text,
    render_find_json,
    render_find_text,
    render_stats_json,
    render_stats_text,
    render_used_by_json,
    render_used_by_text,
)
from quor.tracking.db import REPO_EXPLORE_FILTER_LABEL, get_tracking_db, track_invocation

explore_app = typer.Typer(
    help="Answer repository structure questions from cached repository intelligence — never walks or rebuilds.",
    no_args_is_help=True,
)

_PATH_OPTION = typer.Option(None, "--path", help="Repository root to query (default: current directory).")
_JSON_OPTION = typer.Option(False, "--json", help="Output as JSON instead of plain text.")


def _normalize_target_path(root: Path, raw: str) -> str:
    """Turn a user-supplied file argument into the repo-relative POSIX path
    every cached artifact keys on (`FileSymbols.path`, `Edge.source_file`/
    `target_file`, `RepoIntelState.fingerprints`) — an absolute path is made
    relative to `root` when possible, backslashes are normalized, and a
    leading `./`/redundant `.` segment is dropped. Does not touch the
    filesystem (no `.exists()`/`.resolve()` against the real tree beyond the
    plain string manipulation `Path.resolve()` does in-memory) — a path
    that doesn't match anything in the cache is reported as unknown by the
    caller, exactly like a genuine typo would be, never specially detected
    here."""
    candidate = Path(raw.replace("\\", "/"))
    if candidate.is_absolute():
        with contextlib.suppress(ValueError):
            candidate = candidate.resolve().relative_to(root.resolve())
    posix = PurePosixPath(str(candidate).replace("\\", "/"))
    return "/".join(part for part in posix.parts if part not in (".", ""))


def _load_cache_or_exit(root: Path, *, json_output: bool) -> explorer.ExplorerCache:
    result = explorer.load_cache(root)
    if isinstance(result, CacheUnavailable):
        if json_output:
            typer.echo(render_cache_unavailable_json(result))
        else:
            typer.secho(render_cache_unavailable_text(result), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=ExitCode.GENERAL_ERROR)
    return result


def _track(root: Path, command: str, output: str, t0: float) -> None:
    """Record this invocation the same way `quor map`/`quor symbols`/`quor
    graph`/`quor repo` do — fails open, never affects real output. There is
    no "before" blob (this only reads already-cached data), so
    `original`/`filtered` are recorded equal, mirroring those four
    commands' identical convention. `t0` must be a `time.monotonic()`
    reading taken at the start of the calling command (never a placeholder
    like `0.0`) — `track_invocation()` computes `duration_ms` as
    `(time.monotonic() - t0) * 1000`, so anything else records a wildly
    wrong duration into `quor gain`'s stats."""
    try:
        db = get_tracking_db()
        track_invocation(
            db,
            command=f"{command}: {root.as_posix()}",
            original=output,
            filtered=output,
            filter_name=REPO_EXPLORE_FILTER_LABEL,
            was_passthrough=False,
            t0=t0,
        )
        db.close()
    except Exception:  # noqa: BLE001 — fail-open: tracking must never affect real output
        pass


@explore_app.command("find")
def find_command(
    name: str = typer.Argument(..., help="Exact symbol name to look up (case-sensitive, no fuzzy matching)."),
    path: Path | None = _PATH_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Find a symbol by exact name."""
    t0 = time.monotonic()
    root = resolve_repo_root(path)
    cache = _load_cache_or_exit(root, json_output=json_output)

    result = explorer.find_symbol(cache, name)
    if not result.matches:
        _exit_symbol_not_found(name, json_output=json_output)

    output = render_find_json(result) if json_output else render_find_text(result)
    typer.echo(output)
    _track(root, "Explore find", output, t0)


@explore_app.command("deps")
def deps_command(
    file: str = typer.Argument(..., help="Repository-relative path of the file to inspect."),
    path: Path | None = _PATH_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Show a file's direct (resolved import) dependencies."""
    t0 = time.monotonic()
    root = resolve_repo_root(path)
    cache = _load_cache_or_exit(root, json_output=json_output)

    target = _normalize_target_path(root, file)
    result = explorer.file_dependencies(cache, target)
    if result is None:
        _exit_unknown_file(target, json_output=json_output)

    output = render_deps_json(result) if json_output else render_deps_text(result)
    typer.echo(output)
    _track(root, "Explore deps", output, t0)


@explore_app.command("used-by")
def used_by_command(
    file: str = typer.Argument(..., help="Repository-relative path of the file to inspect."),
    path: Path | None = _PATH_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Show which files directly (via a resolved import) depend on this one."""
    t0 = time.monotonic()
    root = resolve_repo_root(path)
    cache = _load_cache_or_exit(root, json_output=json_output)

    target = _normalize_target_path(root, file)
    result = explorer.file_used_by(cache, target)
    if result is None:
        _exit_unknown_file(target, json_output=json_output)

    output = render_used_by_json(result) if json_output else render_used_by_text(result)
    typer.echo(output)
    _track(root, "Explore used-by", output, t0)


@explore_app.command("file")
def file_command(
    file: str = typer.Argument(..., help="Repository-relative path of the file to summarize."),
    path: Path | None = _PATH_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Show one file's symbol/relationship summary."""
    t0 = time.monotonic()
    root = resolve_repo_root(path)
    cache = _load_cache_or_exit(root, json_output=json_output)

    target = _normalize_target_path(root, file)
    result = explorer.file_summary(cache, target)
    if result is None:
        _exit_unknown_file(target, json_output=json_output)

    output = render_file_json(result) if json_output else render_file_text(result)
    typer.echo(output)
    _track(root, "Explore file", output, t0)


@explore_app.command("stats")
def stats_command(
    path: Path | None = _PATH_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Show repository-wide statistics."""
    t0 = time.monotonic()
    root = resolve_repo_root(path)
    cache = _load_cache_or_exit(root, json_output=json_output)

    result = explorer.repo_stats(cache)
    output = render_stats_json(result) if json_output else render_stats_text(result)
    typer.echo(output)
    _track(root, "Explore stats", output, t0)


def _exit_symbol_not_found(name: str, *, json_output: bool) -> NoReturn:
    message = (
        f'Symbol "{name}" not found. quor explore find does exact-name matching only, by '
        "design — no fuzzy or partial matches. Check the exact spelling and capitalization, "
        "or run `quor symbols` to browse every symbol this repository's cache knows about."
    )
    if json_output:
        typer.echo(orjson.dumps({"error": "symbol_not_found", "message": message}).decode())
    else:
        typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=ExitCode.GENERAL_ERROR)


def _exit_unknown_file(target: str, *, json_output: bool) -> NoReturn:
    message = (
        f'"{target}" is not a file this repository\'s cached intelligence knows about. '
        "Check the path (repository-relative, matching what `quor map`/`quor symbols`/"
        "`quor graph` last scanned), or run `quor repo --rebuild` if the file is new."
    )
    if json_output:
        typer.echo(orjson.dumps({"error": "unknown_file", "message": message}).decode())
    else:
        typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=ExitCode.GENERAL_ERROR)
