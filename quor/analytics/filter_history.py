"""QB-054 — append-only history of per-filter real-usage analytics
snapshots.

Reuses QB-051's `history.json` design pattern (`tests/benchmarks/
history.py`: frozen dataclass entries, `orjson` with `OPT_INDENT_2`,
`load_*`/`append_*`/`build_*` functions) but sourced from the real
tracking DB (`quor.tracking.db.query_filter_analytics`) instead of the
benchmark corpus, and stored under the user's local Quor data directory
(`platformdirs`) rather than the git repo — these are per-machine usage
numbers, not a repo-tracked release artifact, and QB-054 requires "do not
collect any new user information."

This intentionally does **not** replay QB-051's "re-running the same
version replaces its row" dedup rule: `tests/benchmarks/history.json`
itself is untouched by this module (a separate file, a separate format,
never opened here) and its own one-row-per-version behavior is unaffected.
Every call to `append_snapshot()` here adds one new row, unconditionally —
these are point-in-time usage snapshots, not a per-version comparison, so
the sequence of entries *is* the signal `growing_filters()` reads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import orjson
import platformdirs

from quor.tracking.db import PASSTHROUGH_LABEL, FilterAnalyticsReport


def default_history_path() -> Path:
    """Resolved at call time (not import time), mirroring `quor.tracking.
    db.get_tracking_db()`'s own `platformdirs.user_data_dir()` call-time
    pattern — a module-level constant would bind the real path once at
    import and never see a test's `patch("platformdirs.user_data_dir",
    ...)`, which every existing `quor gain` CLI test already relies on."""
    return Path(platformdirs.user_data_dir("quor")) / "filter_analytics_history.json"


@dataclass(frozen=True)
class FilterSnapshot:
    """One filter's stats within one `AnalyticsHistoryEntry` — a rounded,
    serializable copy of `quor.tracking.db.FilterUsage`."""

    filter_name: str
    invocation_count: int
    usage_pct: float
    original_tokens: int
    final_tokens: int
    tokens_saved: int
    avg_compression_pct: float
    passthrough_pct: float
    avg_duration_ms: float


@dataclass(frozen=True)
class AnalyticsHistoryEntry:
    """One analytics run's full per-filter snapshot."""

    recorded_at: str
    days: int
    total_invocations: int
    filters: tuple[FilterSnapshot, ...]


def build_entry(report: FilterAnalyticsReport) -> AnalyticsHistoryEntry:
    """Convert a live `FilterAnalyticsReport` into a serializable snapshot.
    Values are rounded for display stability (mirrors `tests/benchmarks/
    history.py`'s `build_entry`) — the underlying `FilterAnalyticsReport`
    passed in is never mutated."""
    return AnalyticsHistoryEntry(
        recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),  # noqa: UP017
        days=report.days,
        total_invocations=report.total_invocations,
        filters=tuple(
            FilterSnapshot(
                filter_name=f.filter_name,
                invocation_count=f.invocation_count,
                usage_pct=round(f.usage_pct, 2),
                original_tokens=f.original_tokens,
                final_tokens=f.final_tokens,
                tokens_saved=f.tokens_saved,
                avg_compression_pct=round(f.avg_compression_pct, 2),
                passthrough_pct=round(f.passthrough_pct, 2),
                avg_duration_ms=round(f.avg_duration_ms, 3),
            )
            for f in report.filters
        ),
    )


def load_history(path: Path | None = None) -> list[AnalyticsHistoryEntry]:
    path = path if path is not None else default_history_path()
    if not path.exists():
        return []
    data = orjson.loads(path.read_bytes())
    entries: list[AnalyticsHistoryEntry] = []
    for e in data.get("entries", []):
        filters = tuple(FilterSnapshot(**f) for f in e.get("filters", []))
        entries.append(
            AnalyticsHistoryEntry(
                recorded_at=e["recorded_at"],
                days=e["days"],
                total_invocations=e["total_invocations"],
                filters=filters,
            )
        )
    return entries


def append_snapshot(
    entry: AnalyticsHistoryEntry, path: Path | None = None
) -> list[AnalyticsHistoryEntry]:
    """Add `entry` to the end of the history file and persist. Returns the
    full, updated entry list in insertion (chronological) order.

    Unconditional append — no version-keyed replace, unlike QB-051's
    `tests/benchmarks/history.py::append_entry()` (see module docstring for
    why). `path.parent.mkdir` guards the first-ever run, when the user data
    directory may not exist yet.
    """
    path = path if path is not None else default_history_path()
    entries = load_history(path)
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        orjson.dumps({"entries": [asdict(e) for e in entries]}, option=orjson.OPT_INDENT_2)
    )
    return entries


@dataclass(frozen=True)
class UsageGrowth:
    filter_name: str
    oldest_usage_pct: float
    newest_usage_pct: float
    growth_pp: float


def growing_filters(entries: list[AnalyticsHistoryEntry]) -> list[UsageGrowth]:
    """Filters whose share of invocations (`usage_pct`) grew between the
    oldest and newest snapshot in `entries`.

    Compares the two ends of the full observed window, not adjacent pairs
    — a single noisy run between two snapshots shouldn't register as a
    trend. Only filters present in both the oldest and newest snapshot are
    comparable (a filter that only appears in one has no growth to measure
    against). Requires at least two entries; returns `[]` otherwise, same
    as `tests/benchmarks/history.py::detect_regression()`'s own "fewer than
    two entries" guard. Sorted by largest growth first.

    The `PASSTHROUGH_LABEL` bucket is excluded — it isn't a filter (same
    exclusion `quor.analytics.filter_divergence` and `filter_report`'s
    `_real_filters()` already apply to every other view).
    """
    if len(entries) < 2:
        return []
    oldest, newest = entries[0], entries[-1]
    oldest_by_name = {
        f.filter_name: f.usage_pct for f in oldest.filters if f.filter_name != PASSTHROUGH_LABEL
    }
    newest_by_name = {
        f.filter_name: f.usage_pct for f in newest.filters if f.filter_name != PASSTHROUGH_LABEL
    }

    growth = [
        UsageGrowth(
            filter_name=name,
            oldest_usage_pct=oldest_by_name[name],
            newest_usage_pct=newest_by_name[name],
            growth_pp=newest_by_name[name] - oldest_by_name[name],
        )
        for name in oldest_by_name.keys() & newest_by_name.keys()
        if newest_by_name[name] > oldest_by_name[name]
    ]
    return sorted(growth, key=lambda g: g.growth_pp, reverse=True)
