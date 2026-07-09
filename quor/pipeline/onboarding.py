"""Onboarding mode (PA-F08): brief stats to stderr for the first 5 filtered
commands, silent from the 6th onward.

Dispatcher-level only, like tee.py — this module never touches ContentMask,
Pipeline, or any StageHandler. Scope is deliberately global (per machine, in
platformdirs.user_data_dir("quor")), not per-project: onboarding describes a
new user's first experience with the tool itself, not with any one project,
so trying Quor across several projects on day one should still only show
the tip 5 times total, not 5 times per project.

Storage is a single small text file holding one integer counter, written
atomically (tempfile + os.replace, the same pattern used by
quor/cli/commands/init.py's _write_text_atomic) rather than a SQLite state
file like tee's: the stakes of a lost race here are a cosmetic double-print
of the same tip at most once, not data corruption, so the lighter-weight
atomic-file-replace approach is proportionate (unlike tee_state.db, which
needs SQLite's WAL mode specifically to make a read-then-conditionally-write
throttle check safe under concurrent access).

All functions here may raise (OSError, ValueError) — callers are
responsible for fail-open handling, matching the pattern already used by
quor/adapters/dispatcher.py's _track() and quor/pipeline/tee.py's functions.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import platformdirs

MAX_ONBOARDING_COMMANDS = 5
_STATE_FILENAME = "onboarding_count.txt"


def _state_path() -> Path:
    return Path(platformdirs.user_data_dir("quor")) / _STATE_FILENAME


def _read_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        # Corrupted/foreign content in the state file — treat as "never
        # recorded" rather than raising, since losing an exact onboarding
        # count is cosmetic, not a correctness issue worth failing dispatch
        # over the same way a real tracking-DB write would be.
        return 0


def _write_count_atomic(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(count))
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def record_filtered_command() -> int | None:
    """Record one filtered command and return its onboarding sequence
    number (1-indexed), or None if onboarding mode has already ended.

    Increments the persisted counter exactly once per call, whether or not
    it returns a number — so the file always reflects the true count of
    filtered commands seen, even after onboarding mode ends.
    """
    path = _state_path()
    count = _read_count(path)
    count += 1
    _write_count_atomic(path, count)
    return count if count <= MAX_ONBOARDING_COMMANDS else None
