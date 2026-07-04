"""Tee mechanism (ADR-023): cache raw command output before compression.

Dispatcher-level only — this module never touches ContentMask, Pipeline, or
any StageHandler. It reads two plain strings (the true raw subprocess output
and the final output Quor is about to print) and, if they differ, persists
the raw string to a content-addressed file so nothing is irrecoverably lost
to aggressive compression (ADR-031).

Storage: platformdirs.user_data_dir("quor") / "tee" / "{sha256}.txt".
Content-addressed, so identical output across invocations dedupes to one
file. Cleanup deletes files whose mtime is older than `max_age_days`, and is
throttled via a tiny SQLite state file so the directory is not swept on
every single dispatch (see cleanup_tee).

All functions here may raise (OSError, sqlite3.Error) — callers are
responsible for fail-open handling, matching the pattern already used by
quor/adapters/dispatcher.py's _track() and _teardown_plugins().
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import platformdirs

_TEE_SUBDIR = "tee"
_STATE_DB_NAME = "tee_state.db"
_DEFAULT_MAX_AGE_DAYS = 7
_DEFAULT_THROTTLE_HOURS = 24

_CREATE_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tee_cleanup (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_cleanup_at TEXT NOT NULL
)
"""


def content_hash(content: str) -> str:
    """SHA256 hex digest of `content`, per ADR-023."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def tee_dir() -> Path:
    """The tee cache directory: platformdirs.user_data_dir("quor") / "tee"."""
    return Path(platformdirs.user_data_dir("quor")) / _TEE_SUBDIR


def tee_path(content: str) -> Path:
    """Deterministic path for `content`: {tee_dir}/{sha256(content)}.txt."""
    return tee_dir() / f"{content_hash(content)}.txt"


def write_tee(content: str) -> Path:
    """Write `content` to its content-addressed tee file. Idempotent.

    If the file already exists (identical content previously teed), this is
    a cache hit: the write is skipped but the file's mtime is refreshed to
    now, so a content-addressed dedup hit does not go stale under the
    mtime-based retention window in cleanup_tee().
    """
    path = tee_path(content)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        os.utime(path, None)
        return path

    data = content.encode("utf-8")
    try:
        # O_EXCL: fail if another process created it between exists() and
        # here, rather than silently truncating a concurrently-written file.
        # Mode 0o600 restricts to owner read/write on POSIX; on Windows the
        # mode bits are ignored except the read-only flag, which 0o600 does
        # not set — effectively a no-op there, per the platform's own ACL
        # defaults.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.utime(path, None)
        return path

    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def cleanup_tee(
    *,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    throttle_hours: int = _DEFAULT_THROTTLE_HOURS,
) -> None:
    """Delete tee files older than `max_age_days`, throttled via SQLite.

    Quor has no daemon/session concept — every `quor <cmd>` is a fresh
    process — so "at session start" (ADR-023) means "checked at the start of
    some invocation, throttled to at most once per `throttle_hours`". The
    throttle state (last_cleanup_at) lives in its own tiny SQLite file,
    separate from tracking/db.py's TrackingDB, so tee cleanup never contends
    with the tracking background thread's connection.
    """
    now = datetime.now(timezone.utc)  # noqa: UP017
    state_path = Path(platformdirs.user_data_dir("quor")) / _STATE_DB_NAME
    state_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(state_path))
    try:
        conn.execute(_CREATE_STATE_TABLE_SQL)
        row = conn.execute(
            "SELECT last_cleanup_at FROM tee_cleanup WHERE id = 1"
        ).fetchone()

        if row is not None:
            last = datetime.fromisoformat(row[0])
            if now - last < timedelta(hours=throttle_hours):
                return

        _sweep(tee_dir(), max_age_days=max_age_days, now=now)

        conn.execute(
            """INSERT INTO tee_cleanup (id, last_cleanup_at) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET last_cleanup_at = excluded.last_cleanup_at
            """,
            (now.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()


def _sweep(directory: Path, *, max_age_days: int, now: datetime) -> None:
    """Delete files in `directory` whose mtime is older than `max_age_days`."""
    if not directory.exists():
        return

    cutoff = now.timestamp() - max_age_days * 86400
    for path in directory.glob("*.txt"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            # Another process may have already removed it, or it's locked —
            # cleanup is best-effort, never fatal.
            continue
