"""Tee mechanism (ADR-023): cache raw command output before compression.

Dispatcher-level only — this module never touches ContentMask, Pipeline, or
any StageHandler. It reads two plain strings (the true raw subprocess output
and the final output Quor is about to print) and, if they differ, persists
the raw string so nothing is irrecoverably lost to aggressive compression
(ADR-031).

Storage (QB-123): a single bounded SQLite table (`tee_logs`) in
`platformdirs.user_data_dir("quor") / "tee_state.db"` — the same dedicated
SQLite file this module has always used for its throttle/adaptive-disable
state (see "Why a separate tee_state.db" below), now also holding the teed
content itself. Content-addressed by SHA256, so identical output across
invocations dedupes to one row (a cache "hit" refreshes `last_used_at`
instead of inserting a duplicate).

Before QB-123, each teed entry was its own file under
`platformdirs.user_data_dir("quor") / "tee"`. On Windows, creating many
individual small files triggers a real-time Defender scan per file and
fragments the NTFS MFT — a per-file-creation cost that exists independent of
how small the total retained volume is kept. Consolidating into one SQLite
table eliminates that per-entry file-creation cost entirely; the retention
policy itself (age + size, not row count) is unchanged from the file-based
version — see cleanup_tee().

Cleanup deletes rows whose `last_used_at` is older than `max_age_days`, and
is throttled via the same tiny `tee_cleanup` state table so the database is
not swept on every single dispatch (see cleanup_tee()).

Size ceiling (QB-103): the same throttled cleanup pass also enforces a
total-bytes budget (`max_bytes`), evicting oldest-`last_used_at` rows first
once the table's total size exceeds it. This is a hard safety bound against
pathological growth — a scripted or unusually bursty workload producing far
more or larger content than the 7-day age window alone would ever
accumulate under normal, human-paced usage — not a replacement for the
age-based window.

Why a separate tee_state.db instead of reusing tracking/db.py's TrackingDB:
TrackingDB is an async, queue-based writer (record() enqueues and returns;
a background thread does the actual write) with no synchronous
read-then-conditionally-write API, which is what the cleanup throttle check
needs. Routing the throttle check through TrackingDB would mean either
changing its public API to add one, or opening a second raw connection to
quor.db directly — which would contend with TrackingDB's own connection
instead of avoiding contention. That contention risk is sharper now that
tee_logs stores real content payloads, not just a throttle timestamp:
TrackingDB's background thread holds its own WAL connection open for the
life of a long-running process (the MCP server), and interleaving tee's own
read/write traffic into that same file would mean two independent writers
contending for the same locks on every dispatch. Tee also has to work when
the caller passes tracking=None (a supported state in run_dispatch()), so it
cannot depend on a TrackingDB instance existing at all. A second,
single-file SQLite database is the smaller and more decoupled option. The
adaptive-fallback state below (tee_status table) reuses this same
tee_state.db file for the same reasons.

Storing content as a BLOB (UTF-8-encoded bytes) rather than a filesystem
write also removes a footgun the old file-based version had to work around
explicitly: os.open() on Windows defaults to text mode and silently rewrites
"\n" to "\r\n" unless opened with a binary flag. SQLite's BLOB storage has
no such text-mode translation, so there is nothing to work around.

Adaptive fallback: if write_tee() fails with an OSError or sqlite3.Error
(disk full, corporate filesystem/AV policy blocking the SQLite file, etc.)
MAX_CONSECUTIVE_TEE_FAILURES times in a row, tee is persisted as disabled
and no further write attempts are made — retrying a persistent restriction
on every single invocation forever would just generate repeated noise (and,
on some corporate machines, repeated security-software log entries) for no
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
import sqlite3
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import platformdirs

_STATE_DB_NAME = "tee_state.db"
_DEFAULT_MAX_AGE_DAYS = 7
_DEFAULT_THROTTLE_HOURS = 24
# 500 MB (QB-103): comfortably above the tens-of-MB steady state a heavy
# single-developer workload (8-10h/day, hundreds of Bash calls, multiple
# repos, 7-day retention) was projected to reach — see the QB-103
# investigation report — so it never fires under realistic day-to-day use,
# while still capping how large a pathological/scripted workload can grow
# the cache before the next cleanup pass trims it back.
_DEFAULT_MAX_BYTES = 500 * 1024 * 1024

_CREATE_LOGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tee_logs (
    content_hash TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_used_at REAL NOT NULL
)
"""

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
# many consecutive filesystem/SQLite failures trigger adaptive disable.
# Referenced from here, from tests (so they stay correct if this changes),
# and named in docstrings elsewhere (quor/engine/dispatcher.py's _apply_tee)
# rather than restating the number.
MAX_CONSECUTIVE_TEE_FAILURES = 2


@dataclass(frozen=True)
class TeeStatus:
    """Persisted adaptive-fallback status.

    Separate from the user's own on/off preference (QuorUserConfig.tee_enabled
    / FilterConfig.tee) — this tracks whether tee has disabled *itself* after
    repeated write failures, independent of what the user has configured.
    """

    disabled: bool
    consecutive_failures: int
    disabled_reason: str | None


def content_hash(content: str) -> str:
    """SHA256 hex digest of `content`, per ADR-023."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _state_db_path() -> Path:
    return Path(platformdirs.user_data_dir("quor")) / _STATE_DB_NAME


def current_tee_size_bytes() -> int:
    """Sum of every current tee_logs row's size, in bytes.

    Read-only — never deletes anything (that's cleanup_tee()'s job). Used by
    `quor doctor` (QB-103) to report cache size against its configured
    ceiling without triggering an eviction pass itself.
    """
    state_path = _state_db_path()
    if not state_path.exists():
        return 0

    conn = _connect_state_db(state_path)
    try:
        conn.execute(_CREATE_LOGS_TABLE_SQL)
        row = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM tee_logs").fetchone()
    finally:
        conn.close()
    return int(row[0])


def write_tee(content: str) -> str:
    """Persist `content` to tee_logs, keyed by its content hash. Idempotent.

    Returns the SHA256 hex digest (not a filesystem path — see module
    docstring). If a row for this content already exists (identical content
    previously teed), this is a cache hit: no new row is inserted, but
    `last_used_at` is refreshed to now, so a content-addressed dedup hit
    does not go stale under the age-based retention window in
    cleanup_tee().
    """
    digest = content_hash(content)
    data = content.encode("utf-8")
    now = time.time()

    state_path = _state_db_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect_state_db(state_path)
    try:
        conn.execute(_CREATE_LOGS_TABLE_SQL)
        conn.execute(
            """INSERT INTO tee_logs (content_hash, content, size_bytes, last_used_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(content_hash) DO UPDATE SET last_used_at = excluded.last_used_at
            """,
            (digest, data, len(data), now),
        )
        conn.commit()
    finally:
        conn.close()
    return digest


def read_tee(digest: str) -> str | None:
    """Retrieve a previously teed entry by its content hash.

    Returns `None` if no row exists for `digest` (never teed, or already
    evicted by cleanup_tee()) — the recovery footer's contract has always
    been best-effort, not a durability guarantee. Backs `quor doctor
    --show-tee` (QB-123), the replacement for opening the old recovery
    file's path directly now that there is no file to open.
    """
    state_path = _state_db_path()
    if not state_path.exists():
        return None

    conn = _connect_state_db(state_path)
    try:
        conn.execute(_CREATE_LOGS_TABLE_SQL)
        row = conn.execute(
            "SELECT content FROM tee_logs WHERE content_hash = ?", (digest,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    raw = row[0]
    return raw.decode("utf-8") if isinstance(raw, bytes | bytearray) else raw


def get_tee_status() -> TeeStatus:
    """Read the persisted adaptive-fallback state.

    Defaults to enabled/0 failures if the state file or row doesn't exist
    yet — i.e. nothing has ever failed.
    """
    state_path = _state_db_path()
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
    """Record a write_tee() failure (OSError or sqlite3.Error).

    After MAX_CONSECUTIVE_TEE_FAILURES in a row, persists tee as disabled.
    No automatic retry/cooldown — see module docstring "Adaptive fallback".
    """
    state_path = _state_db_path()
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
    state_path = _state_db_path()
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
    state_path = _state_db_path()
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
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> None:
    """Delete tee_logs rows older than `max_age_days`, throttled via SQLite.

    Also enforces a total-size ceiling (`max_bytes`, QB-103): once age
    eviction has run, if the surviving rows still total more than
    `max_bytes`, the oldest-`last_used_at` survivors are deleted next, until
    back within budget. A single row larger than `max_bytes` by itself is
    not special-cased — the ceiling is a hard bound, not a per-row
    exemption, so it is evicted in its turn, like any other entry, once
    it's the one keeping the total over budget.

    Quor has no daemon/session concept — every `quor <cmd>` is a fresh
    process — so "at session start" (ADR-023) means "checked at the start of
    some invocation, throttled to at most once per `throttle_hours`". The
    throttle state (last_cleanup_at) lives in the tee_cleanup table of this
    same tee_state.db file — separate from tracking/db.py's TrackingDB, so
    tee cleanup never contends with the tracking background thread's
    connection. Size eviction shares this same throttle — it is only ever
    considered when a throttled cleanup pass actually runs, never on every
    write.
    """
    now = datetime.now(timezone.utc)  # noqa: UP017
    state_path = _state_db_path()
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

        conn.execute(_CREATE_LOGS_TABLE_SQL)
        _sweep(conn, max_age_days=max_age_days, max_bytes=max_bytes, now=now)

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


def _sweep(conn: sqlite3.Connection, *, max_age_days: int, max_bytes: int, now: datetime) -> None:
    """Delete rows in `tee_logs` older than `max_age_days`, then, if the
    survivors still total more than `max_bytes`, delete oldest-`last_used_at`
    survivors next until back within budget (QB-103).

    Runs on the same connection/transaction the throttle check already
    opened — no second connection, no directory walk.
    """
    cutoff = now.timestamp() - max_age_days * 86400
    conn.execute("DELETE FROM tee_logs WHERE last_used_at < ?", (cutoff,))

    total = int(conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM tee_logs").fetchone()[0])
    if total <= max_bytes:
        return

    # Oldest-last_used_at-first, stopping as soon as we're back within budget.
    survivors = conn.execute(
        "SELECT content_hash, size_bytes FROM tee_logs ORDER BY last_used_at ASC"
    ).fetchall()
    for digest, size in survivors:
        if total <= max_bytes:
            break
        conn.execute("DELETE FROM tee_logs WHERE content_hash = ?", (digest,))
        total -= size
