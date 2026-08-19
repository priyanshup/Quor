"""quor repo — Repository Intelligence Dashboard (QB-076, auto-refreshed as of QB-077).

A fifth exempted utility command, following the exact precedent `quor map`/
`quor symbols`/`quor graph` already established (ADR-037/038/039): a
non-filtering, non-compression reporting command, not one of the six V1
filtering-operation commands.

**QB-076 originally made this command read-only/cache-only** (never calling
`intel.ensure_repo_intelligence()`, on the reasoning that a reporting command
should never trigger a walk/rebuild). **QB-077 deliberately reverses that**:
"users should not think about map/symbols/graph" — `quor repo` is often the
*first* repository-intelligence command a user or AI assistant runs, so it
now goes through `ensure_repo_intelligence()` exactly like `quor map`/
`quor symbols`/`quor graph` already do (onboards automatically on a repo
with no cache yet, reuses the cache instantly when nothing changed, rebuilds
only the affected files otherwise), then reads the freshly-written cache via
`quor.pipeline.repo_profile.dashboard.build_dashboard()` — that function
itself is unchanged, still a pure, cache-only aggregator (see its own
docstring); it just now always sees an up-to-date cache. `--rebuild` forces
a full rebuild, bypassing the cache entirely, mirroring the other three
commands' flag.

The dashboard gains a "Repository Intelligence" status panel (freshness,
cache hit ratio, files scanned/reused/changed, rebuild mode) sourced
directly from the `RepoIntelligence` this call just produced — see
`dashboard_model.RepoIntelligenceStatus`.

**`quor explain`/`quor gain` deliberately do NOT get this same treatment**,
despite QB-077's ticket text listing them alongside `quor repo`: neither
command reads or displays any repository-intelligence data today, and
QB-075's audit already found no qualifying consumption opportunity for
either. Wiring `ensure_repo_intelligence()` into them would add a real
walk-plus-diff (and possibly a multi-second cold build) to every invocation
for zero observable benefit — pure latency, no payoff. Confirmed with the
user; revisit if either command ever actually starts consuming repository
intelligence in its own output.

The old "no cache yet" message/path is kept as a defensive fallback (fail-
open, matching this codebase's conventions elsewhere) but is unreachable in
normal operation now that this command builds the cache itself when missing.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import orjson
import typer
from rich.console import Console

from quor.cli.repo_path import resolve_repo_root
from quor.cli.repo_progress import print_build_summary, progress_echo
from quor.pipeline.repo_profile.dashboard import build_dashboard
from quor.pipeline.repo_profile.dashboard_model import RepoIntelligenceStatus
from quor.pipeline.repo_profile.dashboard_render import print_dashboard, render_json
from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
from quor.pipeline.repo_profile.intel_model import RepoIntelligence
from quor.tracking.db import REPO_DASHBOARD_FILTER_LABEL, get_tracking_db, track_invocation_safe

console = Console(highlight=False)

_NO_CACHE_MESSAGE = (
    "Repository intelligence has not been generated yet for this repository. "
    "Run `quor map` (or `quor symbols` / `quor graph`) once to build it — "
    "`quor repo` will then read that cache directly, with no rebuild of its own."
)

_STATUS_LABELS: dict[str, str] = {
    "cache_hit": "Up to date",
    "incremental": "Refreshed",
    "onboarded": "Rebuilt",
    "full_rebuild": "Rebuilt",
    "version_rebuild": "Rebuilt",
    "corrupted_rebuild": "Rebuilt",
    "forced_rebuild": "Rebuilt",
}

_REBUILD_MODE_LABELS: dict[str, str] = {
    "cache_hit": "Instant",
    "incremental": "Incremental",
    "onboarded": "Full",
    "full_rebuild": "Full",
    "version_rebuild": "Full",
    "corrupted_rebuild": "Full",
    "forced_rebuild": "Full",
}


def repo_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to summarize (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the dashboard as JSON instead of a terminal view."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a full rebuild of repository intelligence, bypassing the cache."
    ),
) -> None:
    """Show a compact repository intelligence dashboard, refreshing the cache first."""
    root = resolve_repo_root(path)
    t0 = time.monotonic()

    intel = ensure_repo_intelligence(root, rebuild=rebuild, echo=progress_echo)
    dashboard = build_dashboard(root)
    if dashboard is None:
        # Defensive fallback only — ensure_repo_intelligence() above just
        # wrote a fresh cache, so this should be unreachable in practice.
        _print_no_cache_message(json_output=json_output)
        raise typer.Exit()

    dashboard = dataclasses.replace(dashboard, intelligence=_build_intelligence_status(intel))

    output = render_json(dashboard)
    if json_output:
        typer.echo(output)
    else:
        print_dashboard(dashboard, console)

    detail = f"{len(dashboard.languages)} language{'s' if len(dashboard.languages) != 1 else ''}"
    print_build_summary(intel, detail, elapsed_seconds=time.monotonic() - t0)

    # Synthesis, not compression — see quor/tracking/db.py's
    # track_invocation_safe() docstring for why original/filtered default
    # equal and this call is fail-open (QB-107).
    track_invocation_safe(
        get_tracking_db,
        command=f"Repo: {root.as_posix()}",
        original=output,
        filter_name=REPO_DASHBOARD_FILTER_LABEL,
        t0=t0,
        close_after=True,
    )


def _build_intelligence_status(intel: RepoIntelligence) -> RepoIntelligenceStatus:
    files_reused = intel.files_scanned - intel.files_reextracted
    if intel.diff is not None:
        changed_files: int | None = (
            len(intel.diff.added)
            + len(intel.diff.modified)
            + len(intel.diff.deleted)
            + len(intel.diff.renamed)
        )
    elif intel.action == "cache_hit":
        changed_files = 0
    else:
        # Full-rebuild-shaped action with no prior state to diff against —
        # "changed files" isn't a meaningful number here.
        changed_files = None

    return RepoIntelligenceStatus(
        status=_STATUS_LABELS.get(intel.action, intel.action),
        cache_hit_ratio=intel.cache_hit_ratio,
        files_scanned=intel.files_scanned,
        files_reused=files_reused,
        changed_files=changed_files,
        rebuild_mode=_REBUILD_MODE_LABELS.get(intel.action, intel.action),
    )


def _print_no_cache_message(*, json_output: bool) -> None:
    if json_output:
        typer.echo(
            orjson.dumps({"error": "no_repository_intelligence", "message": _NO_CACHE_MESSAGE}).decode()
        )
    else:
        typer.secho(_NO_CACHE_MESSAGE, fg=typer.colors.YELLOW)
