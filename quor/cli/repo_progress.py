"""Shared, colorized progress/summary presentation for `quor map`/`quor
symbols`/`quor graph` (QB-074).

Presentation only, mirroring `cli/format_utils.py`'s "never influences what
gets computed" discipline: `progress_echo` colors whatever plain-string
message `intel.ensure_repo_intelligence()` already produces (onboarding
banner, "Scanning repository...", "Finished in X seconds.", ...) without
intel.py itself knowing or caring about color — that module stays a plain,
terminal-agnostic orchestrator, exactly as QB-072 designed it, and every
existing test that asserts on its raw message strings (`test_repo_intel.py`)
is unaffected. `print_build_summary` is new: a single, always-present,
counts-bearing closing line neither the QB-072 onboarding banner nor the
"Finished in X seconds." progress line provided on their own — most
noticeably, a true cache-hit previously printed nothing at all, which
satisfies "reuse the cache silently" but not "give the user a status
summary." Both stay on stderr, exactly like every other QB-072 progress
message, so `--json` output on stdout is never touched.
"""

from __future__ import annotations

import typer

from quor.pipeline.repo_profile.intel_model import BuildAction, RepoIntelligence

_ACTION_LABELS: dict[BuildAction, str] = {
    "onboarded": "built",
    "full_rebuild": "rebuilt",
    "version_rebuild": "rebuilt",
    "corrupted_rebuild": "rebuilt",
    "forced_rebuild": "rebuilt",
    "cache_hit": "cached",
    "incremental": "updated",
}


def progress_echo(message: str) -> None:
    """The `echo=` callback passed to `ensure_repo_intelligence()` — colors
    every progress/onboarding line the same way, always to stderr."""
    typer.secho(message, err=True, fg=typer.colors.CYAN)


def print_build_summary(intel: RepoIntelligence, detail: str, *, elapsed_seconds: float) -> None:
    """Print the one-line, always-shown closing summary.

    `detail` is the command-specific count (e.g. "3 languages", "42
    symbols", "38 edges (12 resolved)") — the files-scanned/action-status
    prefix (shared across all three commands) is built from `intel` itself.
    `elapsed_seconds` is the *command's* own elapsed time (the CLI's own
    `t0`), not `intel.elapsed_seconds` (which only covers the
    `ensure_repo_intelligence()` call) — the more honest "what the user
    actually waited for," including rendering and tracking.
    """
    status = _ACTION_LABELS.get(intel.action, intel.action)
    if intel.action == "incremental":
        count = intel.files_reextracted
        noun = "file" if count == 1 else "files"
        status = f"{status} ({count} {noun} re-parsed)"
    files_noun = "file" if intel.files_scanned == 1 else "files"
    typer.secho(
        f"✓ Done in {elapsed_seconds:.1f}s — {intel.files_scanned} {files_noun} {status}, {detail}",
        err=True,
        fg=typer.colors.GREEN,
    )
