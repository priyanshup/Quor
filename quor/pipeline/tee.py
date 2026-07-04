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

Why a separate tee_state.db instead of reusing tracking/db.py's TrackingDB:
TrackingDB is an async, queue-based writer (record() enqueues and returns;
a background thread does the actual write) with no synchronous
read-then-conditionally-write API, which is what the cleanup throttle check
needs. Routing the throttle check through TrackingDB would mean either
changing its public API to add one, or opening a second raw connection to
quor.db directly — which would contend with TrackingDB's own connection
instead of avoiding contention. Tee also has to work when the caller passes
tracking=None (a supported state in run_dispatch()), so it cannot depend on
a TrackingDB instance existing at all. A second, single-table SQLite file
is the smaller and more decoupled option. The adaptive-fallback state below
(tee_status table) reuses this same tee_state.db file for the same reasons.

Adaptive fallback: if write_tee() fails with an OSError (permission denied,
corporate filesystem policy, disk full, etc.) MAX_CONSECUTIVE_TEE_FAILURES
times in a row, tee is persisted as disabled and no further write attempts
are made — retrying a persistent filesystem restriction on every single
invocation forever would just generate repeated noise (and, on some
corporate machines, repeated security-software log entries) for no
benefit. There is no automatic retry/cooldown: the assumption is that a
persistent restriction will not disappear on its own. Tee stays disabled
until reset_tee_state() is called explicitly (wired to
`quor doctor --reset-tee`).

All functions here may raise (OSError, sqlite3.Error) — callers are
responsible for fail-open handling, matching the pattern already used by
quor/adapters/dispatcher.py's _track() and _teardown_plugins().
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import platformdirs

# os.O_BINARY only exists on Windows; getattr(..., 0) makes the OR below a
# no-op on POSIX, where there is no text/binary distinction. Without this,
# os.open() defaults to text mode on Windows and silently rewrites every
# "\n" to "\r\n" on write — corrupting the tee file relative to the actual
# raw output (violates ADR-023's "no modification" guarantee, and makes the
# on-disk bytes no longer match the SHA256 used to name the file).
_O_BINARY = getattr(os, "O_BINARY", 0)

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

_CREATE_STATUS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tee_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    disabled_reason TEXT
)
"""

# Named constant, not a magic number: the single source of truth for how
# many consecutive filesystem failures trigger adaptive disable. Referenced
# from here, from tests (so they stay correct if this changes), and named
# in docstrings elsewhere (quor/adapters/dispatcher.py's _apply_tee) rather
# than restating the number.
MAX_CONSECUTIVE_TEE_FAILURES = 2


@dataclass(frozen=True)
class TeeStatus:
    """Persisted adaptive-fallback status.

    Separate from the user's own on/off preference (QuorUserConfig.tee_enabled
    / FilterConfig.tee) — this tracks whether tee has disabled *itself* after
    repeated filesystem failures, independent of what the user has configured.
    """

    disabled: bool
    consecutive_failures: int
    disabled_reason: str | None


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
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
    except FileExistsError:
        os.utime(path, None)
        return path

    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def get_tee_status() -> TeeStatus:
    """Read the persisted adaptive-fallback state.

    Defaults to enabled/0 failures if the state file or row doesn't exist
    yet — i.e. nothing has ever failed.
    """
    state_path = Path(platformdirs.user_data_dir("quor")) / _STATE_DB_NAME
    if not state_path.exists():
        return TeeStatus(disabled=False, consecutive_failures=0, disabled_reason=None)

    conn = _connect_state_db(state_path)
    try:
        conn.execute(_CREATE_STATUS_TABLE_SQL)
        row = conn.execute(
            "SELECT consecutive_failures, disabled, disabled_reason FROM tee_status WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return TeeStatus(disabled=False, consecutive_failures=0, disabled_reason=None)
    return TeeStatus(
        disabled=bool(row[1]),
        consecutive_failures=int(row[0]),
        disabled_reason=row[2],
    )


def record_tee_failure(reason: str) -> None:
    """Record a filesystem-caused write_tee() failure.

    After MAX_CONSECUTIVE_TEE_FAILURES in a row, persists tee as disabled.
    No automatic retry/cooldown — see module docstring "Adaptive fallback".
    """
    state_path = Path(platformdirs.user_data_dir("quor")) / _STATE_DB_NAME
    state_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect_state_db(state_path)
    try:
        conn.execute(_CREATE_STATUS_TABLE_SQL)
        row = conn.execute(
            "SELECT consecutive_failures FROM tee_status WHERE id = 1"
        ).fetchone()
        updated = (int(row[0]) if row is not None else 0) + 1
        disabled = updated >= MAX_CONSECUTIVE_TEE_FAILURES

        conn.execute(
            """INSERT INTO tee_status (id, consecutive_failures, disabled, disabled_reason)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 consecutive_failures = excluded.consecutive_failures,
                 disabled = excluded.disabled,
                 disabled_reason = excluded.disabled_reason
            """,
            (updated, int(disabled), reason if disabled else None),
        )
        conn.commit()
    finally:
        conn.close()


def record_tee_success() -> None:
    """Reset the consecutive-failure counter after a successful write_tee().

    Only ever called while tee is still enabled — once disabled, no write
    is attempted at all (callers check get_tee_status().disabled first), so
    this never runs while disabled and never needs to touch that flag.
    """
    state_path = Path(platformdirs.user_data_dir("quor")) / _STATE_DB_NAME
    state_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect_state_db(state_path)
    try:
        conn.execute(_CREATE_STATUS_TABLE_SQL)
        conn.execute(
            """INSERT INTO tee_status (id, consecutive_failures, disabled, disabled_reason)
               VALUES (1, 0, 0, NULL)
               ON CONFLICT(id) DO UPDATE SET consecutive_failures = 0
            """
        )
        conn.commit()
    finally:
        conn.close()


def reset_tee_state() -> None:
    """Explicitly clear the adaptive-disable state and failure counter.

    The only way tee re-enables after an adaptive disable — there is no
    automatic retry (see record_tee_failure()). Wired to the CLI via
    `quor doctor --reset-tee`.
    """
    state_path = Path(platformdirs.user_data_dir("quor")) / _STATE_DB_NAME
    state_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect_state_db(state_path)
    try:
        conn.execute(_CREATE_STATUS_TABLE_SQL)
        conn.execute(
            """INSERT INTO tee_status (id, consecutive_failures, disabled, disabled_reason)
               VALUES (1, 0, 0, NULL)
               ON CONFLICT(id) DO UPDATE SET
                 consecutive_failures = 0,
                 disabled = 0,
                 disabled_reason = NULL
            """
        )
        conn.commit()
    finally:
        conn.close()


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

    conn = _connect_state_db(state_path)
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


def _connect_state_db(state_path: Path) -> sqlite3.Connection:
    """Open the tee state DB with WAL mode, retrying under lock contention.

    Mirrors TrackingDB._connect()'s retry pattern (quor/tracking/db.py):
    PRAGMA journal_mode=WAL requires a brief exclusive lock, which can
    transiently fail if two quor processes race to open this file for the
    first time concurrently. Retry a few times before giving up — the
    throttle check/upsert below still works correctly without WAL, just
    with less concurrent-writer headroom.
    """
    conn = sqlite3.connect(str(state_path))
    for attempt in range(5):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            if attempt == 4:
                warnings.warn(
                    "[quor] could not set WAL mode on tee state db (database locked)",
                    stacklevel=2,
                )
            else:
                time.sleep(0.05 * (attempt + 1))
    return conn


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
