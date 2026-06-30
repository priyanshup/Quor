"""Git-tracked file trust check for project-local filters."""

from __future__ import annotations

import subprocess
from pathlib import Path


def is_git_tracked(path: Path) -> bool:
    """Return True if path is tracked by git (not untracked or ignored)."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            capture_output=True,
            timeout=5.0,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
