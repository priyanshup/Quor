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
from quor.tracking.db import REPO_PROFILE_FILTER_LABEL, get_tracking_db, track_invocation_safe


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
    # No "before" blob to compress against — this is synthesis, not
    # compression (see module docstring) — so original/filtered default to
    # the same value, making this invocation's GainReport.tokens_saved
    # contribution exactly zero (honest) while still surfacing it in
    # total_invocations/tokens_after and (0% compression) `quor gain
    # --filters`' breakdown under the "repo-profile" label, never conflated
    # with a real ContentMask filter's ratio. QB-107: track_invocation_safe
    # itself is fail-open.
    track_invocation_safe(
        get_tracking_db,
        command=f"Map: {root.as_posix()}",
        original=output,
        filter_name=REPO_PROFILE_FILTER_LABEL,
        t0=t0,
        close_after=True,
    )
