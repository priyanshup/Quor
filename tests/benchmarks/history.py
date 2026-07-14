"""QB-039 Deliverable 4 — benchmark history: a simple, append-only format for
comparing overall compression across released versions, plus a pure
regression-detection function a future CI job could call.

Format (`tests/benchmarks/history.json`):

    {
      "entries": [
        {
          "version": "0.4.0",
          "recorded_at": "2026-07-01T12:00:00+00:00",
          "total_cases": 60,
          "overall_compression_pct": 35.3,
          "total_tokens_saved": 9602,
          "per_stage_contribution_pct": {"code_ast_summarize": 14.0, ...},
          "per_ecosystem_compression_pct": {"Python": 31.0, ...}
        },
        ...
      ]
    }

One entry per `quor.__version__` — re-running the suite against the same
installed version overwrites that version's entry (mirrors
`run_benchmarks.py --update-baseline`'s existing "re-running replaces, it
doesn't duplicate" idiom), so history stays one row per release rather than
one row per CI run.

Explicitly NOT wired into any CI workflow by this task (no benchmark step
exists in `.github/workflows/*.yml` today — see backlog.md's QB-039
analytics entry) — `detect_regression()` below is the pure function such a
job would call; `run_benchmarks.py --history` is the local, manual
equivalent in the meantime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import orjson

DEFAULT_HISTORY_PATH = Path(__file__).parent / "history.json"

DEFAULT_REGRESSION_THRESHOLD_PP = 2.0


@dataclass(frozen=True)
class HistoryEntry:
    version: str
    recorded_at: str
    total_cases: int
    overall_compression_pct: float
    total_tokens_saved: int
    per_stage_contribution_pct: dict[str, float]
    per_ecosystem_compression_pct: dict[str, float]


def load_history(path: Path = DEFAULT_HISTORY_PATH) -> list[HistoryEntry]:
    if not path.exists():
        return []
    data = orjson.loads(path.read_bytes())
    return [HistoryEntry(**e) for e in data.get("entries", [])]


def append_entry(entry: HistoryEntry, path: Path = DEFAULT_HISTORY_PATH) -> list[HistoryEntry]:
    """Add or replace `entry` (keyed by `version`) and persist. Returns the
    full, updated entry list in insertion order."""
    entries = [e for e in load_history(path) if e.version != entry.version]
    entries.append(entry)
    path.write_bytes(
        orjson.dumps({"entries": [asdict(e) for e in entries]}, option=orjson.OPT_INDENT_2)
    )
    return entries


def build_entry(
    *,
    version: str,
    total_cases: int,
    overall_compression_pct: float,
    total_tokens_saved: int,
    per_stage_contribution_pct: dict[str, float],
    per_ecosystem_compression_pct: dict[str, float],
) -> HistoryEntry:
    return HistoryEntry(
        version=version,
        recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),  # noqa: UP017
        total_cases=total_cases,
        overall_compression_pct=round(overall_compression_pct, 2),
        total_tokens_saved=total_tokens_saved,
        per_stage_contribution_pct={k: round(v, 2) for k, v in per_stage_contribution_pct.items()},
        per_ecosystem_compression_pct={
            k: round(v, 2) for k, v in per_ecosystem_compression_pct.items()
        },
    )


def render_history_table(entries: list[HistoryEntry]) -> str:
    """The ticket's own "vX.Y.Z / pct / delta" stanza form, one block per entry."""
    lines: list[str] = []
    prev_pct: float | None = None
    for e in entries:
        lines.append(f"v{e.version}")
        lines.append(f"{e.overall_compression_pct:.1f}%")
        if prev_pct is not None:
            delta = e.overall_compression_pct - prev_pct
            lines.append(f"{delta:+.1f}%")
        lines.append("")
        prev_pct = e.overall_compression_pct
    return "\n".join(lines).rstrip() + "\n"


def detect_regression(
    entries: list[HistoryEntry], *, threshold_pp: float = DEFAULT_REGRESSION_THRESHOLD_PP
) -> tuple[bool, str]:
    """Compare the last two entries. Returns (is_regression, message).

    Pure comparison — no filesystem/network access — so a future CI job can
    call it directly against `load_history()`'s output without depending on
    anything else in this module.
    """
    if len(entries) < 2:
        return False, "Fewer than two history entries — nothing to compare yet."
    prev, cur = entries[-2], entries[-1]
    delta = cur.overall_compression_pct - prev.overall_compression_pct
    if delta < -threshold_pp:
        return True, (
            f"Regression: v{prev.version} {prev.overall_compression_pct:.1f}% -> "
            f"v{cur.version} {cur.overall_compression_pct:.1f}% ({delta:+.1f}pp, "
            f"threshold {threshold_pp:.1f}pp)"
        )
    return False, (
        f"No regression: v{prev.version} {prev.overall_compression_pct:.1f}% -> "
        f"v{cur.version} {cur.overall_compression_pct:.1f}% ({delta:+.1f}pp)"
    )
