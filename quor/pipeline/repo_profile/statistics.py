"""Cheap, deterministic aggregate repository statistics (QB-061)."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from quor.pipeline.repo_profile.model import LanguageStat, RepoStatistics

_GIT_TIMEOUT = 5.0


def compute_statistics(
    root: Path, files: list[str], languages: list[LanguageStat]
) -> RepoStatistics:
    total_files = len(files)
    total_directories = len(
        {str(PurePosixPath(f).parent) for f in files if PurePosixPath(f).parent != PurePosixPath(".")}
    )
    primary_language = languages[0].language if languages else None
    git_commit_count = _git_commit_count(root)

    return RepoStatistics(
        total_files=total_files,
        total_directories=total_directories,
        primary_language=primary_language,
        git_commit_count=git_commit_count,
    )


def _git_commit_count(root: Path) -> int | None:
    """Return `git rev-list --count HEAD`, or None if `root` is not a git
    repository, has no commits yet, or `git` itself is unavailable —
    never a fabricated 0."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", "HEAD"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None
