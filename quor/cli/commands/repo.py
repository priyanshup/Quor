"""quor repo — Repository Intelligence Dashboard (QB-076).

A fifth exempted utility command, following the exact precedent `quor map`/
`quor symbols`/`quor graph` already established (ADR-037/038/039): a
non-filtering, non-compression reporting command, not one of the six V1
filtering-operation commands.

Unlike `quor map`/`quor symbols`/`quor graph`, this command never calls
`intel.ensure_repo_intelligence()` — its own spec's hard requirement is that
it must never trigger a repository walk, a re-parse, or a cache rebuild of
any kind. It reads whatever is already cached via
`quor.pipeline.repo_profile.dashboard.build_dashboard()`, which itself only
reads the four on-disk cache files `quor map`/`quor symbols`/`quor graph`
already maintain — see that module's own docstring for the full read-only,
cache-only contract. If no cache exists yet (or it's unreadable), this
prints a short, actionable message pointing at `quor map` instead of a
misleading empty dashboard, and exits 0 (this is expected first-run state,
not an error).
"""

from __future__ import annotations

import time
from pathlib import Path

import orjson
import typer
from rich.console import Console

from quor.cli.repo_path import resolve_repo_root
from quor.pipeline.repo_profile.dashboard import build_dashboard
from quor.pipeline.repo_profile.dashboard_render import print_dashboard, render_json
from quor.tracking.db import REPO_DASHBOARD_FILTER_LABEL, get_tracking_db, track_invocation

console = Console(highlight=False)

_NO_CACHE_MESSAGE = (
    "Repository intelligence has not been generated yet for this repository. "
    "Run `quor map` (or `quor symbols` / `quor graph`) once to build it — "
    "`quor repo` will then read that cache directly, with no rebuild of its own."
)


def repo_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to summarize (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the dashboard as JSON instead of a terminal view."
    ),
) -> None:
    """Show a compact repository intelligence dashboard from the existing cache."""
    root = resolve_repo_root(path)
    t0 = time.monotonic()

    dashboard = build_dashboard(root)
    if dashboard is None:
        _print_no_cache_message(json_output=json_output)
        raise typer.Exit()

    output = render_json(dashboard)
    if json_output:
        typer.echo(output)
    else:
        print_dashboard(dashboard, console)

    _track_repo_invocation(root, output, t0)


def _print_no_cache_message(*, json_output: bool) -> None:
    if json_output:
        typer.echo(
            orjson.dumps({"error": "no_repository_intelligence", "message": _NO_CACHE_MESSAGE}).decode()
        )
    else:
        typer.secho(_NO_CACHE_MESSAGE, fg=typer.colors.YELLOW)


def _track_repo_invocation(root: Path, output: str, t0: float) -> None:
    """Record this invocation in the same tracking DB every other Quor
    producer uses, so `quor gain` reflects `quor repo` usage — mirrors
    `quor map`/`quor symbols`/`quor graph`'s identical tracking call site.

    There is no "before" blob to compress against (this presents already-
    cached data, it doesn't compress anything), so `original`/`filtered`
    are the same value, exactly like those three commands. Fails open like
    every other tracking call site: a tracking-DB error must never affect
    the dashboard output the user already received.
    """
    try:
        db = get_tracking_db()
        track_invocation(
            db,
            command=f"Repo: {root.as_posix()}",
            original=output,
            filtered=output,
            filter_name=REPO_DASHBOARD_FILTER_LABEL,
            was_passthrough=False,
            t0=t0,
        )
        db.close()
    except Exception:  # noqa: BLE001 — fail-open: tracking must never affect real output
        pass
