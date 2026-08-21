"""Retention for the repository-intelligence cache (QB-072/QB-079) — QB-124.

Every repository ever `quor map`/`symbols`/`graph`'d gets a permanent
directory under `intel_store.cache_root()` (5-6 JSON files, atomically
written — see `intel_store.py`'s own docstring). Before QB-124 nothing ever
deleted these: a machine that had mapped many repositories, including
throwaway clones or CI checkouts, accumulated one directory per distinct
repository path forever, with no age eviction, no size ceiling, and no
`quor doctor` visibility — the one genuinely unbounded on-disk store found
in the 2026-08-21 architecture audit (every other cache-like store already
has a retention policy: `quor.db`'s 90-day sweep, tee's age+size sweep).

Mirrors tee's retention shape (age eviction first, then size eviction among
survivors, both throttled) — see `quor/pipeline/tee.py`'s module docstring
for the original rationale. Two differences, both forced by what
repo_intel actually is:

- **Directory-level, not row/file-level.** Tee is one opaque blob per
  content hash; repo_intel is 5-6 *structured* files per repository, each
  with its own load/save contract in `intel_store.py`/`nudge.py`, read
  independently by many call sites. Splitting a single repository's cache
  mid-eviction (keeping `state.json` but deleting `profile.json`, say)
  would silently reintroduce the exact "corruption-as-missing" fallback
  path `intel_store.py` already treats as "cache miss, rebuild" — cheap
  and safe, but pointless to trigger on purpose. Eviction is therefore
  whole-directory: a repository's cache is either entirely present or
  entirely gone, never partially pruned.
- **Freshness is the directory's newest file, not one file's own mtime.**
  A repository re-mapped last week but first cached months ago must read as
  "fresh" — using the oldest or an arbitrary file's mtime would evict an
  actively-used cache purely because `state.json` happens to be rewritten
  less often than `file_intelligence.json` within the same scan.

Throttle state lives in a new `repo_intel_cleanup` table inside
`quor/pipeline/tee.py`'s own `tee_state.db` (via its now-public
`state_db_path()`/`connect_state_db()`) rather than a third small SQLite
file — see `tee.state_db_path()`'s own docstring for why consolidating
here, not introducing another dedicated file, is the deliberate choice.
This is CLI-only cache hygiene (`quor map`/`symbols`/`graph`/`repo`/
`search`/`explore`, via `intel.py::ensure_repo_intelligence()`), never on
the MCP `compress_context`/`get_repo_context` hot path — matching where
the writes it's cleaning up after happen in the first place.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quor.pipeline.repo_profile import intel_store
from quor.pipeline.tee import connect_state_db, state_db_path

_DEFAULT_MAX_AGE_DAYS = 30
"""Longer than tee's 7-day window on purpose: repo intelligence is a
semantic cache worth keeping for an actively-developed repository across a
normal multi-week engagement, not a short-term recovery buffer."""

_DEFAULT_THROTTLE_HOURS = 24
"""Same cadence as tee — this only ever runs once per `quor map`/`symbols`/
`graph`/`repo`/`search`/`explore` invocation, so throttling is about
avoiding a directory walk on every single one of those, not a performance-
critical budget the way the MCP hot path's own throttling is."""

_DEFAULT_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
"""A reasoned starting ceiling, not a measured projection the way tee's
500 MB (QB-103) is — that number came from a real investigation report of
observed steady-state tee volume; no equivalent measurement exists yet for
repo_intel across many repositories. Revisit once real usage data exists,
same as any other default without a cited investigation behind it."""

_CREATE_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS repo_intel_cleanup (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_cleanup_at TEXT NOT NULL
)
"""


def current_repo_intel_size_bytes() -> int:
    """Sum of every byte currently stored under `intel_store.cache_root()`.

    Read-only — never deletes anything (that's `cleanup_repo_intel()`'s
    job). Used by `quor doctor` (QB-124) to report cache size against its
    configured ceiling without triggering an eviction pass itself, mirroring
    `tee.current_tee_size_bytes()`'s same read-only contract.
    """
    root = intel_store.cache_root()
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def cleanup_repo_intel(
    *,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    throttle_hours: int = _DEFAULT_THROTTLE_HOURS,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> None:
    """Delete whole per-repository cache directories older than
    `max_age_days`, throttled via SQLite; if survivors still exceed
    `max_bytes`, delete the least-recently-touched survivors next.

    Fail-open is the caller's responsibility (matches `cleanup_tee()`'s own
    contract) — see `intel.py`'s `_cleanup_repo_intel_safe()` wrapper.
    """
    now = datetime.now(timezone.utc)  # noqa: UP017
    path = state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect_state_db(path)
    try:
        conn.execute(_CREATE_STATE_TABLE_SQL)
        row = conn.execute(
            "SELECT last_cleanup_at FROM repo_intel_cleanup WHERE id = 1"
        ).fetchone()

        if row is not None:
            last = datetime.fromisoformat(row[0])
            if now - last < timedelta(hours=throttle_hours):
                return

        _sweep(intel_store.cache_root(), max_age_days=max_age_days, max_bytes=max_bytes, now=now)

        conn.execute(
            """INSERT INTO repo_intel_cleanup (id, last_cleanup_at) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET last_cleanup_at = excluded.last_cleanup_at
            """,
            (now.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()


def _sweep(cache_root: Path, *, max_age_days: int, max_bytes: int, now: datetime) -> None:
    """Delete whole `<repo_key>` directories under `cache_root` whose newest
    file is older than `max_age_days`; if survivors still total more than
    `max_bytes`, delete oldest-newest-file-first survivors next until back
    within budget. One directory scan, one `stat()` per file — mirrors
    `tee.py`'s pre-QB-123 `_sweep()` shape, adapted from per-file to
    per-directory eviction (see module docstring for why)."""
    if not cache_root.exists():
        return

    cutoff = now.timestamp() - max_age_days * 86400
    survivors: list[tuple[float, int, Path]] = []  # (freshest_mtime, dir_size, dir_path)

    for repo_dir in cache_root.iterdir():
        if not repo_dir.is_dir():
            continue
        try:
            entries = list(repo_dir.iterdir())
        except OSError:
            continue

        freshest_mtime = 0.0
        dir_size = 0
        for entry in entries:
            try:
                st = entry.stat()
            except OSError:
                continue
            dir_size += st.st_size
            freshest_mtime = max(freshest_mtime, st.st_mtime)

        if freshest_mtime < cutoff:
            _remove_dir(repo_dir)
            continue
        survivors.append((freshest_mtime, dir_size, repo_dir))

    total = sum(size for _, size, _ in survivors)
    if total <= max_bytes:
        return

    survivors.sort(key=lambda entry: entry[0])  # oldest freshest-mtime first
    for _mtime, size, repo_dir in survivors:
        if total <= max_bytes:
            break
        _remove_dir(repo_dir)
        total -= size


def _remove_dir(path: Path) -> None:
    """Best-effort recursive delete — cleanup is advisory, never fatal
    (mirrors every OSError-swallowing branch in `tee.py`'s own sweep)."""
    for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        except OSError:
            continue
    with contextlib.suppress(OSError):
        path.rmdir()
