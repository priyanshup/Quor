"""SQLite tracking for Quor pipeline invocations.

Public API:
    InvocationRecord       — frozen dataclass, one per pipeline run
    GainReport             — aggregated token-savings summary
    TrackingDB             — background-thread writer (non-blocking)
    track_invocation()     — shared fail-open recorder for every InvocationRecord
                              producer (Bash dispatcher, Read hook, ...)
    query_gain()           — read-side: produce a GainReport from SQLite
    query_recent_invocations() — read-side: last N invocations for `quor dashboard` (QB-083)
    normalize_project_path() — canonical project identity (query_gain's matching rule)
    get_tracking_db()      — factory: create TrackingDB in the platformdirs data dir
    count_tokens()         — ceil(len(text)/4) estimate (±20%)

Historical note (QB-070): this used to also mirror every record to a
`invocations.jsonl` file alongside quor.db ("dual persistence", ADR-008).
Removed — a repo-wide + installed-usage-directory audit found no reader of
that file anywhere (not `quor gain`, not any other command, not a test
except the ones that only existed to assert the JSONL write itself), while
it grew forever with no retention policy at all (unlike quor.db's 90-day
cleanup below). SQLite remains the single store; nothing that previously
read tracking data changes behavior.
"""

from __future__ import annotations

import contextlib
import math
import queue
import re
import sqlite3
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import platformdirs

_SCHEMA_VERSION = 3
_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# v2: project_key_normalized. CREATE TABLE IF NOT EXISTS in schema.sql only
# defines this column for a brand-new database — it is a no-op against an
# existing `invocations` table from before this version. SQLite has no
# `ALTER TABLE ADD COLUMN IF NOT EXISTS`, so existing databases are migrated
# idempotently via PRAGMA table_info() + a guarded ADD COLUMN.
_PROJECT_IDENTITY_COLUMNS = ("project_key_normalized",)

# v3 (QB-070): idx_invocations_project (project_path, recorded_at) and
# idx_invocations_filter (filter_name, recorded_at) were the schema's
# original two indexes (ADR-008) — superseded once every real query moved
# to scoping by project_key_normalized (v2). A repo-wide grep confirms no
# query anywhere filters by bare project_path or filter_name anymore, so
# these two just pay write-amplification on every INSERT for no read
# benefit. schema.sql no longer creates them for a brand-new database;
# existing databases have them dropped idempotently here, the same
# `IF EXISTS`-guarded pattern _ensure_project_identity_columns() uses for
# v2's column addition.
_OBSOLETE_INDEXES = ("idx_invocations_project", "idx_invocations_filter")

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
    """Token-savings summary returned by query_gain().

    QB-017 (gain hardening): `tokens_saved` is unchanged — still exactly
    `sum(original_tokens - final_tokens)`, the same net figure computed
    since the tracking schema was introduced. `gross_savings` and
    `gross_overhead` are a presentation-only *decomposition* of that same
    net figure, computed by splitting the per-row difference into its
    positive and negative parts before summing:

        gross_savings   = sum(original - final) over rows where it's > 0
        gross_overhead  = sum(final - original) over rows where it's > 0
        tokens_saved  ==  gross_savings - gross_overhead   (exact identity)

    No new column, no schema migration, no change to what's written per
    invocation — this only changes how the existing original_tokens/
    final_tokens columns are aggregated for display. See
    quor/cli/commands/gain.py for how these are surfaced, and QB-017 in
    backlog.md for why a per-row "was this tee overhead?" field was
    considered and deliberately not added (it would require changing the
    dispatcher's tracking call, which is out of scope — see ADR-023/
    ADR-031 on the tee mechanism this overhead most commonly comes from).
    """

    total_invocations: int
    tokens_saved: int              # sum(original_tokens - final_tokens) — unchanged formula
    tokens_before: int             # sum(original_tokens) — for display only
    tokens_after: int              # sum(final_tokens) — for display only
    gross_savings: int             # sum of positive (original - final) rows only
    gross_overhead: int            # sum of positive (final - original) rows only
    negative_row_count: int        # count of rows where final_tokens > original_tokens
    passthrough_count: int
    filter_hit_rate: float         # (total - passthroughs) / total, or 0 if empty
    top_filters: list[tuple[str, int]]  # [(filter_name, tokens_saved)] top 5
    days: int
    read_hook_invocations: int
    """Count of rows in this window whose `command` starts with `"Read: "` —
    i.e. produced by the PostToolUse/Read hook (QB-007D), not Bash. Zero
    means the Read hook has never fired for this project/window, so every
    Read-hook-only filter (`markdown`, `document-text`, `cat-javascript`,
    `cat-typescript`, `cat-tsx`, and Python AST summarization *via Read*)
    could not possibly be represented in the other numbers above, no matter
    how effective those filters are — `quor gain`'s own gain-clarity pass
    uses this to show an explicit note rather than let a `0` blend in
    silently next to filters that *did* get a chance to run."""


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def count_tokens(text: str) -> int:
    """Estimate token count as ceil(len(text) / 4). Accuracy: ±20%."""
    return math.ceil(len(text) / 4)


# ---------------------------------------------------------------------------
# TrackingDB — background-thread writer
# ---------------------------------------------------------------------------


def _ensure_project_identity_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add the v2 project-identity column(s) to an existing
    `invocations` table. `CREATE TABLE IF NOT EXISTS` in schema.sql is a
    no-op against a table that already exists from before v2, so a database
    created under the old schema needs its column(s) added explicitly.
    SQLite has no `ADD COLUMN IF NOT EXISTS`, so PRAGMA table_info() is
    checked first — this makes the call safe to run on every connection,
    not just once."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(invocations)")}
    for column in _PROJECT_IDENTITY_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE invocations ADD COLUMN {column} TEXT")


def _drop_obsolete_indexes(conn: sqlite3.Connection) -> None:
    """v3 (QB-070): drop the two indexes superseded by project_key_normalized.

    `DROP INDEX IF EXISTS` is already idempotent by itself, so — unlike
    `_ensure_project_identity_columns()` — this needs no existence check
    first; it's simply a no-op on a database that never had them (a
    brand-new one, since schema.sql no longer creates them) or already had
    them dropped by a previous run."""
    for index_name in _OBSOLETE_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {index_name}")


_BATCH_MAX_SIZE = 25
"""Commit after this many staged INSERTs, even if the interval below hasn't
elapsed yet — bounds how much a single commit can amortize."""

_BATCH_MAX_INTERVAL_SECONDS = 0.25
"""Commit at most this long after the first not-yet-committed INSERT in the
current batch, even if _BATCH_MAX_SIZE hasn't been reached — so a slow
trickle of records still lands promptly rather than waiting indefinitely
for a batch that may never fill up."""


class TrackingDB:
    """Non-blocking SQLite writer.

    `record()` enqueues the record and returns immediately. The background
    worker thread drains the queue and writes to SQLite, batching commits
    (up to _BATCH_MAX_SIZE records or _BATCH_MAX_INTERVAL_SECONDS, whichever
    comes first) instead of committing once per row. `flush()`/`close()`
    always force a commit of whatever is currently staged before returning,
    so their durability contract is unchanged by batching — the only thing
    batching changes is how long a record can sit staged-but-uncommitted
    while the process keeps running and neither flush() nor close() has
    been called. A hard kill in that window loses the same staged-but-
    uncommitted records a hard kill during the old commit-per-row window
    would already have lost if it landed between record() and the write
    actually happening — this was never a strict per-row durability
    guarantee (see ADR-016: "main hook path never waits for DB writes").
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
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
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001 — a failed connect must not crash this thread silently
            warnings.warn(
                f"[quor] tracking DB unavailable, this session's writes will be dropped: {exc}",
                stacklevel=1,
            )
            return

        pending = 0  # staged (executed, not yet committed) INSERTs
        deadline: float | None = None  # monotonic time the batch must be committed by
        while True:
            timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                # Batch interval elapsed with nothing new arriving — commit
                # what's staged rather than holding it hostage indefinitely.
                conn.commit()
                pending = 0
                deadline = None
                continue

            if item is _STOP:
                conn.commit()  # flush any staged batch before stopping
                break
            if isinstance(item, threading.Event):
                conn.commit()  # flush() must observe every record enqueued so far
                item.set()
                continue

            try:
                self._stage_sqlite_insert(conn, item)
                pending += 1
                if deadline is None:
                    deadline = time.monotonic() + _BATCH_MAX_INTERVAL_SECONDS
                if pending >= _BATCH_MAX_SIZE:
                    conn.commit()
                    pending = 0
                    deadline = None
            except Exception as exc:  # noqa: BLE001
                warnings.warn(f"[quor] tracking write error: {exc}", stacklevel=1)
        conn.close()

    # ------------------------------------------------------------------
    # SQLite helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            # PRAGMA journal_mode=WAL requires a brief exclusive lock. If another
            # connection already has the DB open, this can transiently fail. Retry
            # a few times before giving up — WAL mode is still likely already set.
            for attempt in range(5):
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError:
                    conn.rollback()
                    if attempt == 4:
                        warnings.warn(
                            "[quor] could not set WAL mode (database locked); "
                            "concurrent tracking writes may be slower",
                            stacklevel=1,
                        )
                    else:
                        time.sleep(0.05 * (attempt + 1))
            conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema_and_cleanup(conn)
        except BaseException:
            # Whatever failed, this connection is unusable — close it before
            # propagating so its lock (if any) can't linger until GC gets
            # around to it (see _init_schema_and_cleanup's docstring for the
            # bug this closes: an unguarded OperationalError here used to
            # leave a half-initialized, un-closed sqlite3.Connection behind).
            conn.close()
            raise
        return conn

    def _init_schema_and_cleanup(self, conn: sqlite3.Connection) -> None:
        """Apply the schema and delete stale records, retrying as one unit
        on a transient lock.

        Two TrackingDB instances initializing against the same fresh
        database at nearly the same moment — two Claude Code sessions
        starting together, or two writer threads in a test — can collide
        here just as easily as on the WAL PRAGMA above, which is the only
        statement that used to be retried. Left unguarded, the losing
        writer's `sqlite3.OperationalError` propagated straight out of
        `_connect()` uncaught, silently killing its worker thread (see
        `_worker()`) and leaking its connection — the underlying cause of a
        real, observed CI failure (`TestConcurrentWrites` intermittently
        hit `database is locked` on a *separate* read connection, because
        the crashed writer's connection was never closed and could still be
        holding a lock). `conn.rollback()` before each retry clears any
        transaction a partially-executed statement left open, so a retry
        never starts from a dirty state.
        """
        for attempt in range(5):
            try:
                self._apply_schema(conn)
                self._cleanup_old_records(conn)
                return
            except sqlite3.OperationalError:
                conn.rollback()
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        """Create tables if they don't exist and record schema migration."""
        conn.executescript(_SCHEMA_SQL)
        _ensure_project_identity_columns(conn)
        _drop_obsolete_indexes(conn)
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

    def _stage_sqlite_insert(self, conn: sqlite3.Connection, rec: InvocationRecord) -> None:
        """Execute this record's INSERT on `conn` without committing.

        Named _stage_* (not _write_*) since QB-070's batching means the
        statement is only *staged* here — the caller (_worker) decides when
        to actually conn.commit(), up to _BATCH_MAX_SIZE records or
        _BATCH_MAX_INTERVAL_SECONDS later.
        """
        d = rec.to_dict()
        # project_key_normalized is computed here, at the single point every
        # record passes through on its way to SQLite — not by changing
        # InvocationRecord's fields or dispatcher.py's call site, which stay
        # exactly as they were. Every row written from now on already
        # carries its own precomputed identity; query_gain never needs to
        # re-derive it from project_path for a row written this way.
        d["project_key_normalized"] = normalize_project_path(rec.project_path)
        conn.execute(
            """INSERT INTO invocations
               (command, project_path, original_tokens, final_tokens,
                filter_name, was_passthrough, duration_ms, recorded_at, schema_version,
                project_key_normalized)
               VALUES
               (:command, :project_path, :original_tokens, :final_tokens,
                :filter_name, :was_passthrough, :duration_ms, :recorded_at, :schema_version,
                :project_key_normalized)
            """,
            d,
        )


# ---------------------------------------------------------------------------
# Shared write-side helper — one InvocationRecord per producer call
# ---------------------------------------------------------------------------


def track_invocation(
    tracking: TrackingDB | None,
    *,
    command: str,
    original: str,
    filtered: str,
    filter_name: str | None,
    was_passthrough: bool,
    t0: float,
) -> None:
    """Build an `InvocationRecord` from one pipeline run and enqueue it.

    The single, shared fail-open recorder for every producer of
    `InvocationRecord` — originally `dispatcher.py`'s private `_track()`
    helper (Bash), promoted here so `quor/adapters/claude_read.py` (Read,
    QB-007D) can call the exact same logic instead of duplicating it.
    `tracking=None` is a no-op, and any exception (including one raised by
    `tracking` itself) is swallowed with a warning — a producer's own output
    must never be affected by a tracking failure.
    """
    if tracking is None:
        return
    try:
        rec = InvocationRecord(
            command=command,
            project_path=Path.cwd().as_posix(),
            original_tokens=count_tokens(original),
            final_tokens=count_tokens(filtered),
            filter_name=filter_name,
            was_passthrough=was_passthrough,
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        tracking.record(rec)
    except Exception as exc:  # noqa: BLE001
        warnings.warn(f"[quor] tracking record error: {exc}", stacklevel=2)


# ---------------------------------------------------------------------------
# Read-side: query_gain
# ---------------------------------------------------------------------------


def normalize_project_path(path: str | Path) -> str:
    """Canonical project identity: the single source of truth for what "the
    same project" means. Python owns this definition completely.

    Three rules, applied together:
      1. case-insensitive   — Windows drive letters/segments can be reported
         with different casing by different shells (Git Bash's MSYS layer
         vs. native PowerShell/cmd) for the identical physical directory.
      2. POSIX-style          — always forward slashes, matching how the
         write path (dispatcher.py) already stores `Path.cwd().as_posix()`.
      3. trailing-slash-insensitive — "/proj" and "/proj/" are the same
         project.

    This is the single, exclusive implementation of the identity rule —
    nothing else in this module re-derives it, including SQL:
      - Write side: TrackingDB._write_sqlite() calls it once per record to
        populate the precomputed `project_key_normalized` column (schema v2).
      - Read side: query_gain() calls it once on its own input to produce
        `project_key`, compared directly against that precomputed column.
      - Backfill: historical rows written before this column existed have
        it as NULL. query_gain() lazily backfills them by registering this
        exact function as a SQL callable (`conn.create_function(...)`) and
        running `UPDATE ... SET project_key_normalized =
        normalize_project_path(project_path) WHERE ... IS NULL` — the
        backfill *calls this function*, it does not re-implement its rule
        in SQL syntax. This guarantees the backfilled value can never
        diverge from what this function would compute for the same input,
        including edge cases a hand-written SQL approximation would miss
        (Unicode case-folding, multiple internal separators, backslashes).
    """
    posix = path.as_posix() if isinstance(path, Path) else Path(path).as_posix()
    return posix.rstrip("/").lower()


# A normalized key that is empty ("", from "/") or a bare drive letter
# ("c:", from "C:/" or "c:") has no directory segment of its own — scoping a
# query to it would turn the subdirectory LIKE pattern into a match-everything
# wildcard ("" -> "/%" matches every POSIX-style path; "c:" -> "c:/%" matches
# every project on that entire drive). Verified: querying "C:/" against three
# unrelated sibling projects returned all three. A key with at least one real
# segment ("/proj" or "c:/proj") is unaffected by this check.
_BARE_DRIVE_RE = re.compile(r"^[a-z]:$")


def _is_degenerate_project_key(key: str) -> bool:
    """True if `key` has no directory segment of its own (empty, or a bare
    drive letter) — too broad to safely scope a query."""
    return key == "" or bool(_BARE_DRIVE_RE.fullmatch(key))


# SQLite's LIKE (unlike GLOB) supports an ESCAPE clause. "%" and "_" are its
# only wildcard characters, but "_" in particular is extremely common in
# real directory names ("my_project") — the standard technique is to
# backslash-escape both, plus the escape character itself, and declare
# `ESCAPE '\'` on the LIKE clause. Without this, a real project path
# containing "_" would have that character silently reinterpreted as "match
# any single character" instead of literal text.
_LIKE_ESCAPE_TABLE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})
_LIKE_ESCAPE_CLAUSE = "ESCAPE '\\'"


def _escape_like(value: str) -> str:
    """Escape SQLite LIKE metacharacters (%, _) so `value` matches only
    literally. Only ever applied to the *path* portion of a LIKE prefix
    pattern — never to the deliberate wildcard suffix ("/%"), and never to
    the equality branch's parameter, since `=` does not interpret these
    characters at all and escaping it would break the match (the stored
    column contains the literal, unescaped key)."""
    return value.translate(_LIKE_ESCAPE_TABLE)


def query_gain(
    db_path: Path,
    project_path: Path,
    days: int = 30,
    since: datetime | None = None,
) -> GainReport:
    """Return a GainReport aggregated from SQLite for the given project + window.

    `since`, when given, overrides `days` entirely: the query is scoped to
    `recorded_at >= since` (a literal timestamp) instead of a relative
    `-{days} days` window. Used by `quor dashboard` (QB-083) to show
    "since I started watching" rather than a calendar window — `days` is
    still accepted (and still returned on the report) for callers that pass
    neither, so every existing caller is unaffected. `since` is formatted
    with the same `isoformat(timespec="seconds")` convention
    `InvocationRecord.recorded_at` already uses, so the plain string
    comparison SQLite performs sorts correctly against stored rows.
    """
    if not db_path.exists():
        return GainReport(
            total_invocations=0,
            tokens_saved=0,
            tokens_before=0,
            tokens_after=0,
            gross_savings=0,
            gross_overhead=0,
            negative_row_count=0,
            passthrough_count=0,
            filter_hit_rate=0.0,
            top_filters=[],
            days=days,
            read_hook_invocations=0,
        )

    project_key = normalize_project_path(project_path)
    if _is_degenerate_project_key(project_key):
        raise ValueError(
            f"project_path {str(project_path)!r} normalizes to {project_key!r}, "
            "which has no directory segment of its own and is too broad to "
            "safely scope a query (it would match every project under that "
            "root/drive). Pass a specific project directory instead."
        )
    # The equality branch compares the literal, unescaped project_key against
    # the literal, unescaped precomputed column — LIKE metacharacters have no
    # special meaning under `=` at all. The LIKE branch's path portion must
    # be escaped so a real directory name containing % or _ is matched
    # literally rather than reinterpreted as a wildcard; only the deliberate
    # trailing "/%" wildcard suffix is left unescaped.
    subdir_pattern = f"{_escape_like(project_key)}/%"
    if since is not None:
        cutoff_expr = "?"
        cutoff_param: str = since.astimezone(UTC).isoformat(timespec="seconds")
    else:
        cutoff_expr = "datetime('now', ?)"
        cutoff_param = f"-{days} days"
    project_filter = (
        f"(project_key_normalized = ? OR project_key_normalized LIKE ? {_LIKE_ESCAPE_CLAUSE})"
    )

    # contextlib.closing, not `with sqlite3.connect(...) as conn:` — a
    # sqlite3.Connection used as its own context manager only commits/rolls
    # back the transaction on exit, it does NOT close the connection (a
    # common Python sqlite3 gotcha). Left as a bare `with` before, this
    # connection was only ever released by GC — the direct source of most
    # of the "unclosed database" ResourceWarnings observed across the test
    # suite. Every write below already has its own explicit conn.commit(),
    # so nothing here relied on Connection.__exit__'s implicit commit.
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row

        # query_gain() connects directly, independent of TrackingDB — an
        # existing database created under the pre-v2 schema (with no writer
        # having run yet in this process) would not have this column at
        # all, so the backfill UPDATE below needs it to exist first. Same
        # idempotent guard TrackingDB._apply_schema() uses.
        _ensure_project_identity_columns(conn)

        # Lazy backfill (schema v2): populate project_key_normalized for any
        # row written before this column existed. Idempotent and cheap once
        # complete — the WHERE clause matches zero rows on every subsequent
        # call, and is covered by the same index used for the real query
        # below. normalize_project_path is registered as a SQL function so
        # this UPDATE *calls* the one authoritative implementation rather
        # than re-deriving an approximation of its rule in SQL syntax — a
        # hand-written `LOWER(RTRIM(x, '/'))` would silently diverge for
        # inputs normalize_project_path handles that plain string functions
        # cannot: non-ASCII case-folding (SQLite's built-in LOWER() only
        # folds ASCII), stray backslashes, or repeated internal separators.
        # This is still a single set-based UPDATE — one statement, one
        # transaction, applied to every matching row by SQLite's own engine
        # — not a Python loop issuing one UPDATE per row; a registered
        # scalar function is invoked per-row internally by SQLite the same
        # way a built-in function like LOWER() already is.
        conn.create_function("normalize_project_path", 1, normalize_project_path)
        conn.execute(
            """UPDATE invocations
               SET project_key_normalized = normalize_project_path(project_path)
               WHERE project_key_normalized IS NULL
            """
        )
        conn.commit()

        # Aggregate totals. gross_savings/gross_overhead split the same
        # per-row (original_tokens - final_tokens) difference already used
        # for `saved` into its positive and negative parts before summing —
        # a presentation-only decomposition of the existing net figure, not
        # a new measurement (see GainReport's docstring).
        row = conn.execute(
            f"""SELECT
                 COUNT(*)                              AS total,
                 COALESCE(SUM(original_tokens - final_tokens), 0) AS saved,
                 COALESCE(SUM(original_tokens), 0)     AS before_sum,
                 COALESCE(SUM(final_tokens), 0)         AS after_sum,
                 COALESCE(SUM(CASE WHEN original_tokens - final_tokens > 0
                                    THEN original_tokens - final_tokens ELSE 0 END), 0)
                                                        AS gross_savings,
                 COALESCE(SUM(CASE WHEN final_tokens - original_tokens > 0
                                    THEN final_tokens - original_tokens ELSE 0 END), 0)
                                                        AS gross_overhead,
                 COALESCE(SUM(CASE WHEN final_tokens > original_tokens
                                    THEN 1 ELSE 0 END), 0)
                                                        AS negative_rows,
                 SUM(was_passthrough)                  AS passthroughs
               FROM invocations
               WHERE {project_filter}
                 AND recorded_at  >= {cutoff_expr}
            """,
            (project_key, subdir_pattern, cutoff_param),
        ).fetchone()

        total = int(row["total"])
        saved = int(row["saved"])
        tokens_before = int(row["before_sum"])
        tokens_after = int(row["after_sum"])
        gross_savings = int(row["gross_savings"])
        gross_overhead = int(row["gross_overhead"])
        negative_row_count = int(row["negative_rows"])
        passthroughs = int(row["passthroughs"] or 0)
        hit_rate = (total - passthroughs) / total if total else 0.0

        # Top 5 filters by tokens saved
        top_rows = conn.execute(
            f"""SELECT filter_name, SUM(original_tokens - final_tokens) AS saved_sum
               FROM invocations
               WHERE {project_filter}
                 AND recorded_at  >= {cutoff_expr}
                 AND filter_name  IS NOT NULL
               GROUP BY filter_name
               ORDER BY saved_sum DESC
               LIMIT 5
            """,
            (project_key, subdir_pattern, cutoff_param),
        ).fetchall()

        # Read-hook activity in this same window (see GainReport.
        # read_hook_invocations' own docstring for why this is tracked
        # separately rather than left implicit): "Read: " is the exact,
        # literal prefix claude_read.py's own command column always uses
        # (f"Read: {file_path}") — not a heuristic, the one and only format
        # any Read-hook row has ever been written with (QB-007D).
        read_hook_row = conn.execute(
            f"""SELECT COUNT(*) AS n
               FROM invocations
               WHERE {project_filter}
                 AND recorded_at  >= {cutoff_expr}
                 AND command LIKE 'Read: %'
            """,
            (project_key, subdir_pattern, cutoff_param),
        ).fetchone()

    top_filters = [(r["filter_name"], int(r["saved_sum"])) for r in top_rows]
    read_hook_invocations = int(read_hook_row["n"])

    return GainReport(
        total_invocations=total,
        tokens_saved=saved,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        gross_savings=gross_savings,
        gross_overhead=gross_overhead,
        negative_row_count=negative_row_count,
        passthrough_count=passthroughs,
        filter_hit_rate=hit_rate,
        top_filters=top_filters,
        days=days,
        read_hook_invocations=read_hook_invocations,
    )


# ---------------------------------------------------------------------------
# Read-side: query_recent_invocations (QB-083 — quor dashboard)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecentInvocation:
    """One row for `quor dashboard`'s recent-activity feed. Metadata only —
    the same columns `InvocationRecord` already writes, never the actual
    command output content (ANTI_GOALS.md #4)."""

    command: str
    filter_name: str | None
    original_tokens: int
    final_tokens: int
    recorded_at: str


def query_recent_invocations(
    db_path: Path,
    project_path: Path,
    since: datetime,
    limit: int = 10,
) -> list[RecentInvocation]:
    """Return up to `limit` most-recent invocations at/after `since`, newest
    first. Mirrors `query_gain()`'s project-scoping (same normalize/escape
    helpers, same `project_key_normalized` column) — a second, narrower view
    over the same rows, not a second data source."""
    if not db_path.exists():
        return []

    project_key = normalize_project_path(project_path)
    if _is_degenerate_project_key(project_key):
        raise ValueError(
            f"project_path {str(project_path)!r} normalizes to {project_key!r}, "
            "which has no directory segment of its own and is too broad to "
            "safely scope a query (it would match every project under that "
            "root/drive). Pass a specific project directory instead."
        )
    subdir_pattern = f"{_escape_like(project_key)}/%"
    since_param = since.astimezone(UTC).isoformat(timespec="seconds")
    project_filter = (
        f"(project_key_normalized = ? OR project_key_normalized LIKE ? {_LIKE_ESCAPE_CLAUSE})"
    )

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_project_identity_columns(conn)
        conn.create_function("normalize_project_path", 1, normalize_project_path)
        conn.execute(
            """UPDATE invocations
               SET project_key_normalized = normalize_project_path(project_path)
               WHERE project_key_normalized IS NULL
            """
        )
        conn.commit()

        rows = conn.execute(
            f"""SELECT command, filter_name, original_tokens, final_tokens, recorded_at
               FROM invocations
               WHERE {project_filter}
                 AND recorded_at >= ?
               ORDER BY recorded_at DESC
               LIMIT ?
            """,
            (project_key, subdir_pattern, since_param, limit),
        ).fetchall()

    return [
        RecentInvocation(
            command=r["command"],
            filter_name=r["filter_name"],
            original_tokens=int(r["original_tokens"]),
            final_tokens=int(r["final_tokens"]),
            recorded_at=r["recorded_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Read-side: query_filter_analytics (QB-054)
# ---------------------------------------------------------------------------

# The synthetic bucket label for rows with no matching filter
# (`filter_name IS NULL`, always paired with `was_passthrough=1` — see
# `InvocationRecord`'s own docstring). Grouping these under one label
# rather than dropping them keeps `query_filter_analytics()` honest about
# *all* invocations in the window, per QB-054's "reuse the existing
# invocations table exactly as it is" requirement — a per-filter usage
# report that silently excluded unmatched commands would overstate every
# real filter's `usage_pct`.
PASSTHROUGH_LABEL = "(no filter matched)"

# QB-061: the synthetic `filter_name` `quor map` tracks its invocations
# under (see `quor/cli/commands/map.py::_track_map_invocation`). Like
# `PASSTHROUGH_LABEL`, this is not a real ContentMask filter — `quor map`
# is synthesis, not compression, so its `original_tokens`/`final_tokens`
# are deliberately recorded equal (net-zero contribution, by design, not a
# defect). Analytics that flag "near-zero/negative compression" as a
# problem (`quor.analytics.filter_divergence.flag_low_performers`) must
# exclude this label the same way they already exclude PASSTHROUGH_LABEL —
# otherwise a real compression regression (mypy, ruff) would be reported
# side-by-side with an expected, by-design zero, diluting the signal.
REPO_PROFILE_FILTER_LABEL = "repo-profile"

# QB-066: the synthetic `filter_name` `quor symbols` tracks its invocations
# under (see `quor/cli/commands/symbols.py::_track_symbols_invocation`).
# Same reasoning as `REPO_PROFILE_FILTER_LABEL` above, applied to the
# second synthesis-not-compression command: a repository symbol index has
# no "before" blob either, so `original_tokens`/`final_tokens` are recorded
# equal by design, and `flag_low_performers` must exclude this label too.
REPO_SYMBOLS_FILTER_LABEL = "repo-symbols"

# QB-067: the synthetic `filter_name` `quor graph` tracks its invocations
# under (see `quor/cli/commands/graph.py::_track_graph_invocation`). Same
# reasoning as `REPO_PROFILE_FILTER_LABEL`/`REPO_SYMBOLS_FILTER_LABEL`
# above, applied to the third synthesis-not-compression command: a
# repository dependency graph has no "before" blob either, so
# `original_tokens`/`final_tokens` are recorded equal by design, and
# `flag_low_performers` must exclude this label too.
REPO_GRAPH_FILTER_LABEL = "repo-graph"

# QB-076: the synthetic `filter_name` `quor repo` tracks its invocations
# under (see `quor/cli/commands/repo.py::_track_repo_invocation`). Same
# reasoning as `REPO_PROFILE_FILTER_LABEL`/`REPO_SYMBOLS_FILTER_LABEL`/
# `REPO_GRAPH_FILTER_LABEL` above, applied to the fourth synthesis-not-
# compression command: the dashboard only presents already-cached
# repository intelligence, so it has no "before" blob either —
# `original_tokens`/`final_tokens` are recorded equal by design, and
# `flag_low_performers` must exclude this label too.
REPO_DASHBOARD_FILTER_LABEL = "repo-dashboard"

# QB-078: the synthetic `filter_name` `quor explore` tracks its invocations
# under (see `quor\cli\commands\explore.py::_track()`). Same reasoning as
# `REPO_PROFILE_FILTER_LABEL`/`REPO_SYMBOLS_FILTER_LABEL`/
# `REPO_GRAPH_FILTER_LABEL`/`REPO_DASHBOARD_FILTER_LABEL` above, applied to
# the fifth synthesis-not-compression command: `quor explore` only reads
# already-cached repository intelligence, so it has no "before" blob
# either — `original_tokens`/`final_tokens` are recorded equal by design,
# and `flag_low_performers` must exclude this label too.
REPO_EXPLORE_FILTER_LABEL = "repo-explore"

# QB-080: the synthetic `filter_name` `quor search` tracks its invocations
# under (see `quor\cli\commands\search.py::_track()`). Same reasoning as
# `REPO_PROFILE_FILTER_LABEL`/`REPO_SYMBOLS_FILTER_LABEL`/
# `REPO_GRAPH_FILTER_LABEL`/`REPO_DASHBOARD_FILTER_LABEL`/
# `REPO_EXPLORE_FILTER_LABEL` above, applied to the sixth synthesis-not-
# compression command: `quor search` only reads already-cached repository
# intelligence, so it has no "before" blob either — `original_tokens`/
# `final_tokens` are recorded equal by design, and `flag_low_performers`
# must exclude this label too.
REPO_SEARCH_FILTER_LABEL = "repo-search"


@dataclass(frozen=True)
class FilterUsage:
    """Aggregated stats for one `filter_name` (or `PASSTHROUGH_LABEL`) over
    every invocation in the queried project/window.

    Every field is computed directly from `invocations` columns that
    already exist — no new data collected, no schema change. `was_passthrough`
    is always 0 for a real filter_name group (a filter is only ever recorded
    when one matched — see `InvocationRecord`'s docstring), so
    `passthrough_pct` is always exactly 0.0 for those rows by construction;
    it is only ever non-zero for the `PASSTHROUGH_LABEL` group, where it is
    always exactly 100.0. Reported per-group anyway (not hardcoded) so the
    numbers are read straight from SQL, not asserted by Python.
    """

    filter_name: str
    invocation_count: int
    usage_pct: float                # invocation_count / total_invocations * 100
    original_tokens: int
    final_tokens: int
    tokens_saved: int                # original_tokens - final_tokens
    avg_compression_pct: float       # tokens_saved / original_tokens * 100 (aggregate ratio, matches tests/benchmarks/benchmark_runner.py's own per-category convention)
    per_invocation_avg_pct: float
    """QB-065: the true arithmetic mean of each invocation's own
    `(original-final)/original*100`, NOT the same figure as
    `avg_compression_pct` above — that field is deliberately an aggregate,
    sum-based ratio (`test_avg_compression_pct_is_aggregate_ratio_not_mean_of_rows`
    pins this on purpose, matching `benchmark_runner.py`'s own per-category
    convention), which a handful of very large invocations can dominate
    while most real invocations are still individually negative. Confirmed
    in production: `ruff`/`generic` both showed a healthy-looking aggregate
    ratio (+20.8%/+43.8%) while this field was negative (-12.3%/-2.8%) for
    the same window — see `quor/analytics/filter_divergence.py`'s module
    docstring. Rows where `original_tokens` is 0 are excluded from this
    mean (there is no percentage to average), same guard `avg_compression_pct`
    already applies implicitly via its own zero-numerator handling.
    """
    passthrough_pct: float
    avg_duration_ms: float


@dataclass(frozen=True)
class FilterAnalyticsReport:
    """Per-filter breakdown for one project/window — the QB-054 counterpart
    to `GainReport`'s single aggregate summary."""

    total_invocations: int
    days: int
    filters: tuple[FilterUsage, ...]  # includes PASSTHROUGH_LABEL if any row is unmatched


def query_filter_analytics(
    db_path: Path,
    project_path: Path,
    days: int = 30,
) -> FilterAnalyticsReport:
    """Return a `FilterAnalyticsReport` grouped by `filter_name`, read from
    SQLite. Mirrors `query_gain()`'s project-scoping/backfill logic exactly
    (same helper functions, same window semantics) — this is a second view
    over the same rows, not a second data source.
    """
    if not db_path.exists():
        return FilterAnalyticsReport(total_invocations=0, days=days, filters=())

    project_key = normalize_project_path(project_path)
    if _is_degenerate_project_key(project_key):
        raise ValueError(
            f"project_path {str(project_path)!r} normalizes to {project_key!r}, "
            "which has no directory segment of its own and is too broad to "
            "safely scope a query (it would match every project under that "
            "root/drive). Pass a specific project directory instead."
        )
    subdir_pattern = f"{_escape_like(project_key)}/%"
    since = f"-{days} days"
    project_filter = (
        f"(project_key_normalized = ? OR project_key_normalized LIKE ? {_LIKE_ESCAPE_CLAUSE})"
    )

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row

        _ensure_project_identity_columns(conn)
        conn.create_function("normalize_project_path", 1, normalize_project_path)
        conn.execute(
            """UPDATE invocations
               SET project_key_normalized = normalize_project_path(project_path)
               WHERE project_key_normalized IS NULL
            """
        )
        conn.commit()

        total_row = conn.execute(
            f"""SELECT COUNT(*) AS n
               FROM invocations
               WHERE {project_filter}
                 AND recorded_at >= datetime('now', ?)
            """,
            (project_key, subdir_pattern, since),
        ).fetchone()
        total = int(total_row["n"])

        rows = conn.execute(
            f"""SELECT
                 COALESCE(filter_name, ?)               AS label,
                 COUNT(*)                                AS n,
                 COALESCE(SUM(original_tokens), 0)       AS orig_sum,
                 COALESCE(SUM(final_tokens), 0)          AS final_sum,
                 COALESCE(SUM(was_passthrough), 0)       AS passthroughs,
                 COALESCE(AVG(duration_ms), 0.0)         AS avg_duration,
                 COALESCE(AVG(
                     CASE WHEN original_tokens > 0
                          THEN (original_tokens - final_tokens) * 100.0 / original_tokens
                     END
                 ), 0.0)                                 AS avg_pct_per_row
               FROM invocations
               WHERE {project_filter}
                 AND recorded_at >= datetime('now', ?)
               GROUP BY label
               ORDER BY n DESC, label ASC
            """,
            (PASSTHROUGH_LABEL, project_key, subdir_pattern, since),
        ).fetchall()

    filters = tuple(
        FilterUsage(
            filter_name=r["label"],
            invocation_count=int(r["n"]),
            usage_pct=(int(r["n"]) / total * 100) if total else 0.0,
            original_tokens=int(r["orig_sum"]),
            final_tokens=int(r["final_sum"]),
            tokens_saved=int(r["orig_sum"]) - int(r["final_sum"]),
            avg_compression_pct=(
                (int(r["orig_sum"]) - int(r["final_sum"])) / int(r["orig_sum"]) * 100
                if r["orig_sum"]
                else 0.0
            ),
            per_invocation_avg_pct=float(r["avg_pct_per_row"]),
            passthrough_pct=(int(r["passthroughs"]) / int(r["n"]) * 100) if r["n"] else 0.0,
            avg_duration_ms=float(r["avg_duration"]),
        )
        for r in rows
    )

    return FilterAnalyticsReport(total_invocations=total, days=days, filters=filters)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_tracking_db() -> TrackingDB:
    """Create a TrackingDB backed by the platformdirs user data directory."""
    data_dir = Path(platformdirs.user_data_dir("quor"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return TrackingDB(db_path=data_dir / "quor.db")
