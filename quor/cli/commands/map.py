"""quor map — deterministic Repository Context Profile (QB-061).

A new capability, not a filter: it reads many files and synthesizes an
orientation document (languages, frameworks, build system, package
manager, entry points, ...) that never existed verbatim anywhere in the
repo. See `docs/design/QB-061-repo-context-profile.md` for the full
architectural rationale for why this lives outside the ContentMask
pipeline and is exposed as its own command, the same category of
exception `quor schema` already established (a non-filtering utility
command, not one of the six V1 filtering-operation commands).

As of QB-072 (Automatic Repository Intelligence), this command no longer
calls `profiler.build_profile()` directly — it goes through
`intel.ensure_repo_intelligence()`, which transparently handles first-time
onboarding, cache reuse, and incremental rebuilds; see that module's own
docstring for the full design. `--rebuild` forces a full rebuild,
bypassing the cache entirely.

As of QB-074, `--path` is validated up front (`repo_path.resolve_repo_root`)
— a nonexistent path or a file passed where a directory was expected now
exits with a clear, actionable message instead of silently producing an
empty profile — and a colorized progress/summary presentation
(`repo_progress.py`) reports elapsed time and a language count on stderr.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from quor.cli.repo_path import resolve_repo_root
from quor.cli.repo_progress import print_build_summary, progress_echo
from quor.pipeline.repo_profile.intel import ensure_repo_intelligence
from quor.pipeline.repo_profile.render import render_json, render_markdown
from quor.tracking.db import REPO_PROFILE_FILTER_LABEL, get_tracking_db, track_invocation


def map_command(
    path: Path | None = typer.Option(
        None, "--path", help="Repository root to profile (default: current directory)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output the profile as JSON instead of Markdown."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force a full rebuild of repository intelligence, bypassing the cache."
    ),
) -> None:
    """Generate a deterministic repository context profile."""
    root = resolve_repo_root(path)
    t0 = time.monotonic()

    intel = ensure_repo_intelligence(root, rebuild=rebuild, echo=progress_echo)
    profile = intel.profile
    output = render_json(profile) if json_output else render_markdown(profile)

    language_count = len(profile.languages)
    detail = f"{language_count} language{'s' if language_count != 1 else ''}"
    print_build_summary(intel, detail, elapsed_seconds=time.monotonic() - t0)

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
