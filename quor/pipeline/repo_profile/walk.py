"""Deterministic file enumeration for the Repository Context Profile (QB-061).

Primary path: `git ls-files --cached --others --exclude-standard`, the same
subprocess-to-git pattern `quor/filters/trust.py::is_git_tracked` already
uses — deterministic, respects `.gitignore`/`.git/info/exclude` for free,
and needs no hand-rolled ignore-pattern walking. Falls back to `os.walk`
with a small hardcoded skip-set when there is no git repository (or `git`
itself is unavailable).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from quor.pipeline.repo_profile._longpath import to_long_path

_GIT_TIMEOUT = 10.0

# Only used by the no-git fallback — git's own .gitignore handling already
# covers this for the primary path. Mirrors the skip-set already used
# elsewhere in this codebase for the equivalent no-git situation (see
# docs/design/QB-061-repo-context-profile.md §3.1).
_FALLBACK_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
        "egg-info",
    }
)


@dataclass(frozen=True)
class WalkResult:
    """Result of walking a repository."""

    files: list[str]
    """Sorted, repo-relative POSIX paths."""

    used_git: bool
    """True if `git ls-files` succeeded; False if the os.walk fallback ran."""


def walk_repository(root: Path) -> WalkResult:
    """Return every discoverable file under `root`, as sorted, repo-relative
    POSIX paths. Deterministic given the same repo state: re-running this
    against an unchanged repo always returns the identical list."""
    tracked = _git_ls_files(root)
    if tracked is not None:
        return WalkResult(files=tracked, used_git=True)
    return WalkResult(files=_walk_fallback(root), used_git=False)


def _git_ls_files(root: Path) -> list[str] | None:
    """Return `git ls-files`'s output as a sorted list, or None if `root`
    is not inside a git repository (or `git` itself is unavailable)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return sorted(line for line in result.stdout.splitlines() if line)


def _walk_fallback(root: Path) -> list[str]:
    # QB-110: extended-length-prefix the starting root unconditionally
    # (force=True — see to_long_path()'s own docstring for why a walk root
    # can't be threshold-gated on its own current length), not just each
    # leaf file read elsewhere in this package — os.walk builds every
    # deeper dirpath via plain string concatenation off the root it's
    # given, so a prefixed root propagates the prefix (and its MAX_PATH
    # immunity) through the whole recursive walk automatically. long_root
    # is used consistently as both the walk root and the relative_to()
    # anchor below.
    long_root = to_long_path(root, force=True)
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(long_root):
        dirnames[:] = [d for d in dirnames if d not in _FALLBACK_SKIP_DIRS]
        for name in filenames:
            rel = Path(dirpath, name).relative_to(long_root).as_posix()
            results.append(rel)
    return sorted(results)
