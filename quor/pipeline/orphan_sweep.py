"""Startup orphan sweeper (QB-123): best-effort cleanup of leftover
`quor_*` directories under the OS temp directory from a past crashed
process.

Investigation for QB-123 found no code path in Quor that currently creates
a `quor_*` directory under `tempfile.gettempdir()` — teed content now lives
entirely in SQLite (quor/pipeline/tee.py), and atomic_io.py's `mkstemp()`
writes land inside the *target* directory, not TEMP, and are always renamed
or cleaned up immediately. This sweeper is deliberately defensive rather
than a fix for an observed leak: cheap insurance against whatever future
feature (or an external tool) starts dropping directories here, not a
response to anything currently happening.

Fail-open and non-blocking by design: a single non-recursive glob plus one
stat() per match, so the cost is O(1) in the overwhelmingly common case of
zero matches. Never raises — any error here must not affect the caller
(`quor/cli/main.py`'s root callback, `quor/mcp/server.py`'s `main()`).
"""

from __future__ import annotations

import shutil
import tempfile
import time
import warnings
from pathlib import Path

_ORPHAN_GLOB = "quor_*"
_MAX_AGE_SECONDS = 60 * 60  # 1 hour


def sweep_orphaned_temp_dirs(*, max_age_seconds: int = _MAX_AGE_SECONDS) -> None:
    """Delete `quor_*`-prefixed directories under the OS temp dir whose
    mtime is older than `max_age_seconds`.

    Fail-open: any error (an unreadable temp dir, a locked entry) is
    swallowed with a warning, never raised — a startup hygiene pass must
    never block or fail the caller it runs ahead of.
    """
    try:
        temp_root = Path(tempfile.gettempdir())
        cutoff = time.time() - max_age_seconds
        for entry in temp_root.glob(_ORPHAN_GLOB):
            try:
                if not entry.is_dir() or entry.stat().st_mtime >= cutoff:
                    continue
                shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    except Exception as exc:  # noqa: BLE001 — startup sweep must never block the caller
        warnings.warn(f"[quor] orphan temp-dir sweep error: {exc}", stacklevel=2)
