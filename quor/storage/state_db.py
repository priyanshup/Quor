"""Shared low-level SQLite connection helper (QB-127).

Every small on-disk state store Quor owns — tee's recovery cache and
repo-intel's retention throttle (both in `tee_state.db`, see
`state_db_path()` below), and the invocation-tracking database
(`quor/tracking/db.py`'s `quor.db`) — used to open its own SQLite
connection independently, each re-implementing the same WAL-mode
retry-on-lock loop with small, accidental differences (tee's own
pre-QB-127 `connect_state_db()` never set `PRAGMA synchronous=NORMAL`;
`TrackingDB._connect()` did). `connect_with_wal_retry()` below is the one
place that logic lives now, so every store gets identical durability/
concurrency behavior and a setting changed once applies everywhere.

`state_db_path()`/`connect_state_db()` live here too — not because every
store shares `tee_state.db` (`TrackingDB` still owns a separate `quor.db`
file; see `quor/pipeline/tee.py`'s module docstring, "Why a separate
tee_state.db instead of reusing tracking/db.py's TrackingDB", for the
connection-contention reasoning behind that split, which this move does
not change), but because `tee_state.db` is already shared between two
independent callers (tee's own recovery cache and
`repo_profile/intel_cleanup.py`'s retention throttle) and belongs to
neither of them specifically.
"""

from __future__ import annotations

import sqlite3
import time
import warnings
from pathlib import Path

import platformdirs

_STATE_DB_NAME = "tee_state.db"


def state_db_path() -> Path:
    """Path to the small shared-state SQLite file (tee cache + repo-intel
    retention throttle). See module docstring for why these two stores
    share one file instead of each owning a dedicated one."""
    return Path(platformdirs.user_data_dir("quor")) / _STATE_DB_NAME


def connect_with_wal_retry(
    db_path: Path,
    *,
    check_same_thread: bool = True,
    max_attempts: int = 5,
) -> sqlite3.Connection:
    """Open a SQLite connection at `db_path` with WAL journaling and
    `synchronous=NORMAL`, retrying under transient lock contention.

    `PRAGMA journal_mode=WAL` requires a brief exclusive lock, which can
    transiently fail if another connection (a second `quor` CLI process,
    the MCP server, a concurrent test writer) already has the file open
    for the very first time. Retried up to `max_attempts` times before
    giving up with a warning — WAL mode is very likely already set by
    whichever connection won the race, so a failure here doesn't block
    the caller, only its own attempt to (re-)confirm the mode.

    `synchronous=NORMAL` trades the strongest durability guarantee (a
    process kill or power loss between commit and the WAL checkpoint
    could lose the most recent transaction) for real write throughput —
    an acceptable trade for every store this backs, since none of them is
    a system of record whose loss corrupts anything: the caller either
    re-derives the row on the next run or simply misses one data point.

    `check_same_thread=False` is for a connection a background worker
    thread will reuse across multiple calls (`TrackingDB`'s writer
    thread); every other caller keeps the default (`True`), matching the
    open-use-close-per-call pattern the rest of this codebase uses.

    Raises on failure to open the connection itself. If anything the
    caller does *after* this returns fails, closing `conn` is the
    caller's responsibility — this function only guarantees the
    connection isn't leaked if *it* fails partway through its own WAL/
    synchronous setup.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    try:
        for attempt in range(max_attempts):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError:
                conn.rollback()
                if attempt == max_attempts - 1:
                    warnings.warn(
                        f"[quor] could not set WAL mode on {db_path.name} "
                        "(database locked); concurrent writes may be slower",
                        stacklevel=2,
                    )
                else:
                    time.sleep(0.05 * (attempt + 1))
        conn.execute("PRAGMA synchronous=NORMAL")
    except BaseException:
        conn.close()
        raise
    return conn


def connect_state_db(db_path: Path) -> sqlite3.Connection:
    """`connect_with_wal_retry()` against the shared state file — kept as
    its own name since every tee.py/intel_cleanup.py call site already
    calls it this way; `db_path` is still required (not defaulted to
    `state_db_path()`) so callers keep resolving the path once themselves
    rather than this function silently recomputing it a second time."""
    return connect_with_wal_retry(db_path)
