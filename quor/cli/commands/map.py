"""quor map — deterministic Repository Context Profile (QB-061).

A new capability, not a filter: it reads many files and synthesizes an
orientation document (languages, frameworks, build system, package
manager, entry points, ...) that never existed verbatim anywhere in the
repo. See `docs/design/QB-061-repo-context-profile.md` for the full
architectural rationale for why this lives outside the ContentMask
pipeline and is exposed as its own command, the same category of
exception `quor schema` already established (a non-filtering utility
command, not one of the six V1 filtering-operation commands).
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from quor.pipeline.repo_profile.profiler import build_profile
from quor.pipeline.repo_profile.render import render_json, render_markdown
from quor.tracking.db import REPO_PROFILE_FILTER_LABEL, get_tracking_db, track_invocation


def map_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to profile (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the profile as JSON instead of Markdown."
    ),
) -> None:
    """Generate a deterministic repository context profile."""
    root = (path or Path.cwd()).resolve()
    t0 = time.monotonic()

    profile = build_profile(root)
    output = render_json(profile) if json_output else render_markdown(profile)

    typer.echo(output)
    _track_map_invocation(root, output, t0)


def _track_map_invocation(root: Path, output: str, t0: float) -> None:
    """Record this invocation in the same tracking DB every other Quor
    producer uses, so `quor gain` reflects `quor map` usage.

    There is no "before" blob to compress against — this is synthesis, not
    compression (see module docstring) — so `original` and `filtered` are
    deliberately passed as the *same* value. That makes this invocation's
    contribution to GainReport.tokens_saved exactly zero (honest: `quor map`
    doesn't save tokens, it avoids a multi-call discovery sequence that
    Quor's tracking has no way to measure directly) while still surfacing
    the invocation itself in `total_invocations`/`tokens_after` and (with
    zero % compression) in `quor gain --filters`' per-filter breakdown under
    the "repo-profile" label — never conflated with a real ContentMask
    filter's compression ratio.

    Fails open like every other tracking call site: a tracking-DB error
    must never affect the profile output the user already received.
    """
    try:
        db = get_tracking_db()
        track_invocation(
            db,
            command=f"Map: {root.as_posix()}",
            original=output,
            filtered=output,
            filter_name=REPO_PROFILE_FILTER_LABEL,
            was_passthrough=False,
            t0=t0,
        )
        db.close()
    except Exception:  # noqa: BLE001 — fail-open: tracking must never affect real output
        pass
