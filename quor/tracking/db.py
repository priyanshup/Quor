"""SQLite + JSONL tracking for Quor pipeline invocations.

Public API:
    InvocationRecord  — frozen dataclass, one per pipeline run
    GainReport        — aggregated token-savings summary
    TrackingDB        — background-thread writer (non-blocking)
    query_gain()      — read-side: produce a GainReport from SQLite
    get_tracking_db() — factory: create TrackingDB in the platformdirs data dir
    count_tokens()    — ceil(len(text)/4) estimate (±20%)
"""

from __future__ import annotations

import math
import queue
import sqlite3
import threading
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson
import platformdirs

_SCHEMA_VERSION = 1
_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# Sentinel: put this on the queue to stop the worker thread
_STOP = object()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvocationRecord:
    """One pipeline invocation to be persisted."""

    command: str
    project_path: str          # Path.as_posix() — no backslashes
    original_tokens: int
    final_tokens: int
    filter_name: str | None    # None when was_passthrough is True
    was_passthrough: bool
    duration_ms: float
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017
    )
    schema_version: int = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "project_path": self.project_path,
            "original_tokens": self.original_tokens,
            "final_tokens": self.final_tokens,
            "filter_name": self.filter_name,
            "was_passthrough": int(self.was_passthrough),
            "duration_ms": self.duration_ms,
            "recorded_at": self.recorded_at,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class GainReport:
    """Token-savings summary returned by query_gain()."""

    total_invocations: int
    tokens_saved: int              # sum(original_tokens - final_tokens)
    tokens_before: int             # sum(original_tokens) — for display only
    tokens_after: int              # sum(final_tokens) — for display only
    passthrough_count: int
    filter_hit_rate: float         # (total - passthroughs) / total, or 0 if empty
    top_filters: list[tuple[str, int]]  # [(filter_name, tokens_saved)] top 5
    days: int


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def count_tokens(text: str) -> int:
    """Estimate token count as ceil(len(text) / 4). Accuracy: ±20%."""
    return math.ceil(len(text) / 4)


# ---------------------------------------------------------------------------
# TrackingDB — background-thread writer
# ---------------------------------------------------------------------------


class TrackingDB:
    """Non-blocking SQLite + JSONL writer.

    `record()` enqueues the record and returns immediately.
    The background worker thread drains the queue and writes both stores.
    The main hook path never waits for DB writes (ADR-016).
    """

    def __init__(self, db_path: Path, jsonl_path: Path | None = None) -> None:
        self._db_path = db_path
        self._jsonl_path = jsonl_path
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._worker, name="quor-tracking", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, rec: InvocationRecord) -> None:
        """Enqueue a record for background persistence. Never blocks."""
        self._queue.put(rec)

    def close(self, timeout: float = 2.0) -> None:
        """Signal worker to stop and wait for it to drain the queue."""
        self._queue.put(_STOP)
        self._thread.join(timeout=timeout)

    def flush(self, timeout: float = 2.0) -> None:
        """Block until the queue is drained (used in tests to verify writes)."""
        done = threading.Event()
        self._queue.put(done)
        done.wait(timeout=timeout)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        conn = self._connect()
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            if isinstance(item, threading.Event):
                item.set()
                continue
            try:
                self._write_sqlite(conn, item)
                if self._jsonl_path is not None:
                    self._write_jsonl(item)
            except Exception as exc:  # noqa: BLE001
                warnings.warn(f"[quor] tracking write error: {exc}", stacklevel=1)
        conn.close()

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        # PRAGMA journal_mode=WAL requires a brief exclusive lock. If another
        # connection already has the DB open, this can transiently fail. Retry
        # a few times before giving up — WAL mode is still likely already set.
        for attempt in range(5):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError:
                if attempt == 4:
                    warnings.warn(
                        "[quor] could not set WAL mode (database locked); "
                        "concurrent tracking writes may be slower",
                        stacklevel=1,
                    )
                else:
                    import time

                    time.sleep(0.05 * (attempt + 1))
        conn.execute("PRAGMA synchronous=NORMAL")
        self._apply_schema(conn)
        self._cleanup_old_records(conn)
        return conn

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        """Create tables if they don't exist and record schema migration."""
        conn.executescript(_SCHEMA_SQL)
        # Ensure migration row exists for current version
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
        conn.commit()

    def _cleanup_old_records(self, conn: sqlite3.Connection) -> None:
        """Delete records older than 90 days."""
        conn.execute(
            "DELETE FROM invocations WHERE recorded_at < datetime('now', '-90 days')"
        )
        conn.commit()

    def _write_sqlite(self, conn: sqlite3.Connection, rec: InvocationRecord) -> None:
        d = rec.to_dict()
        conn.execute(
            """INSERT INTO invocations
               (command, project_path, original_tokens, final_tokens,
                filter_name, was_passthrough, duration_ms, recorded_at, schema_version)
               VALUES
               (:command, :project_path, :original_tokens, :final_tokens,
                :filter_name, :was_passthrough, :duration_ms, :recorded_at, :schema_version)
            """,
            d,
        )
        conn.commit()

    # ------------------------------------------------------------------
    # JSONL helper
    # ------------------------------------------------------------------

    def _write_jsonl(self, rec: InvocationRecord) -> None:
        assert self._jsonl_path is not None
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        line = orjson.dumps(rec.to_dict()) + b"\n"
        with open(self._jsonl_path, "ab") as fh:
            fh.write(line)


# ---------------------------------------------------------------------------
# Read-side: query_gain
# ---------------------------------------------------------------------------


def query_gain(
    db_path: Path,
    project_path: Path,
    days: int = 30,
) -> GainReport:
    """Return a GainReport aggregated from SQLite for the given project + window."""
    if not db_path.exists():
        return GainReport(
            total_invocations=0,
            tokens_saved=0,
            tokens_before=0,
            tokens_after=0,
            passthrough_count=0,
            filter_hit_rate=0.0,
            top_filters=[],
            days=days,
        )

    project_posix = project_path.as_posix()
    glob_pattern = f"{project_posix}*"
    since = f"-{days} days"

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # Aggregate totals
        row = conn.execute(
            """SELECT
                 COUNT(*)                              AS total,
                 COALESCE(SUM(original_tokens - final_tokens), 0) AS saved,
                 COALESCE(SUM(original_tokens), 0)     AS before_sum,
                 COALESCE(SUM(final_tokens), 0)         AS after_sum,
                 SUM(was_passthrough)                  AS passthroughs
               FROM invocations
               WHERE project_path GLOB ?
                 AND recorded_at  >= datetime('now', ?)
            """,
            (glob_pattern, since),
        ).fetchone()

        total = int(row["total"])
        saved = int(row["saved"])
        tokens_before = int(row["before_sum"])
        tokens_after = int(row["after_sum"])
        passthroughs = int(row["passthroughs"] or 0)
        hit_rate = (total - passthroughs) / total if total else 0.0

        # Top 5 filters by tokens saved
        top_rows = conn.execute(
            """SELECT filter_name, SUM(original_tokens - final_tokens) AS saved_sum
               FROM invocations
               WHERE project_path GLOB ?
                 AND recorded_at  >= datetime('now', ?)
                 AND filter_name  IS NOT NULL
               GROUP BY filter_name
               ORDER BY saved_sum DESC
               LIMIT 5
            """,
            (glob_pattern, since),
        ).fetchall()

    top_filters = [(r["filter_name"], int(r["saved_sum"])) for r in top_rows]

    return GainReport(
        total_invocations=total,
        tokens_saved=saved,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        passthrough_count=passthroughs,
        filter_hit_rate=hit_rate,
        top_filters=top_filters,
        days=days,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_tracking_db() -> TrackingDB:
    """Create a TrackingDB backed by the platformdirs user data directory."""
    data_dir = Path(platformdirs.user_data_dir("quor"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return TrackingDB(
        db_path=data_dir / "quor.db",
        jsonl_path=data_dir / "invocations.jsonl",
    )
