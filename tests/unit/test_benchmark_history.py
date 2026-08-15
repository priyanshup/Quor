"""QB-047 Phase 1 — test coverage for `tests/benchmarks/history.py`.

This module had zero test coverage before this ticket (confirmed during the
QB-047 investigation, `docs/design/QB-047-real-world-benchmark-corpus-
investigation.md` §3/§12) despite being fully-implemented, real code other
work (release tooling) is now expected to depend on. Mirrors
`tests/unit/test_filter_analytics.py`'s `TestHistoryPersistence`/
`TestGrowingFilters` style (class-per-function, `tmp_path` fixture,
class-based grouping) for the structurally near-identical QB-054 modules.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from tests.benchmarks.history import (
    DEFAULT_REGRESSION_THRESHOLD_PP,
    HistoryEntry,
    append_entry,
    build_entry,
    detect_regression,
    load_history,
    render_history_table,
)


def _entry(
    version: str,
    overall_compression_pct: float = 35.0,
    *,
    total_cases: int = 100,
    total_tokens_saved: int = 1000,
) -> HistoryEntry:
    return HistoryEntry(
        version=version,
        recorded_at="2026-08-01T00:00:00+00:00",
        total_cases=total_cases,
        overall_compression_pct=overall_compression_pct,
        total_tokens_saved=total_tokens_saved,
        per_stage_contribution_pct={"strip_lines": 50.0, "max_tokens": 50.0},
        per_ecosystem_compression_pct={"Git": 20.0, "Python": 40.0},
    )


class TestBuildEntry:
    def test_rounds_percentages(self) -> None:
        entry = build_entry(
            version="0.5.0",
            total_cases=153,
            overall_compression_pct=35.94444,
            total_tokens_saved=20549,
            per_stage_contribution_pct={"strip_lines": 19.4999, "max_tokens": 18.601},
            per_ecosystem_compression_pct={"Git": 22.777},
        )
        assert entry.overall_compression_pct == 35.94
        assert entry.per_stage_contribution_pct == {"strip_lines": 19.5, "max_tokens": 18.6}
        assert entry.per_ecosystem_compression_pct == {"Git": 22.78}

    def test_recorded_at_is_set(self) -> None:
        entry = build_entry(
            version="0.5.0",
            total_cases=1,
            overall_compression_pct=0.0,
            total_tokens_saved=0,
            per_stage_contribution_pct={},
            per_ecosystem_compression_pct={},
        )
        assert entry.recorded_at  # non-empty ISO timestamp
        assert entry.version == "0.5.0"


class TestLoadHistory:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_history(tmp_path / "history.json") == []

    def test_round_trips_a_written_file(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        append_entry(_entry("0.1.0"), path)
        loaded = load_history(path)
        assert len(loaded) == 1
        assert loaded[0].version == "0.1.0"

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        """Not silently swallowed — mirrors `benchmark_runner.load_baseline()`'s
        own equivalent (no special-cased error handling for corrupt JSON
        either). A corrupt history.json is a real, actionable problem for
        whoever runs `--history`; failing loudly is the existing convention
        for every other JSON artifact this suite reads."""
        path = tmp_path / "history.json"
        path.write_bytes(b"{not valid json")
        with pytest.raises(orjson.JSONDecodeError):
            load_history(path)

    def test_schema_mismatched_entry_raises(self, tmp_path: Path) -> None:
        """An entry missing a required field fails loudly (TypeError from
        HistoryEntry(**e)) rather than silently producing a partial/garbage
        entry."""
        path = tmp_path / "history.json"
        path.write_bytes(orjson.dumps({"entries": [{"version": "0.1.0"}]}))
        with pytest.raises(TypeError):
            load_history(path)


class TestAppendEntry:
    def test_first_run_creates_file(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        assert not path.exists()
        append_entry(_entry("0.1.0"), path)
        assert path.exists()
        assert [e.version for e in load_history(path)] == ["0.1.0"]

    def test_second_run_new_version_appends(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        append_entry(_entry("0.1.0"), path)
        append_entry(_entry("0.2.0"), path)
        assert [e.version for e in load_history(path)] == ["0.1.0", "0.2.0"]

    def test_multiple_releases_preserve_chronological_order(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        for version in ("0.1.0", "0.1.1", "0.2.0", "0.3.0", "0.4.0", "0.4.1", "0.5.0"):
            append_entry(_entry(version), path)
        assert [e.version for e in load_history(path)] == [
            "0.1.0",
            "0.1.1",
            "0.2.0",
            "0.3.0",
            "0.4.0",
            "0.4.1",
            "0.5.0",
        ]

    def test_rerunning_same_version_replaces_not_duplicates(self, tmp_path: Path) -> None:
        """Avoid duplicate entries for identical/re-run versions — the
        existing, documented contract (`history.py`'s own module docstring:
        "re-running the same version replaces its row")."""
        path = tmp_path / "history.json"
        append_entry(_entry("0.5.0", overall_compression_pct=30.0), path)
        append_entry(_entry("0.5.0", overall_compression_pct=35.9), path)
        entries = load_history(path)
        assert len(entries) == 1
        assert entries[0].overall_compression_pct == 35.9

    def test_rerunning_an_older_version_does_not_reorder_history(self, tmp_path: Path) -> None:
        """Regression test for the ordering bug found while hardening this
        module for QB-047 Phase 1: `append_entry()` used to remove-then-
        append on a version match, which silently moved a re-run of an
        *older* version to the end of the list — after newer versions
        already recorded — breaking chronological order and
        `detect_regression()`'s "compare the last two entries" assumption.
        A re-run must replace in place, at its original position."""
        path = tmp_path / "history.json"
        append_entry(_entry("0.1.0"), path)
        append_entry(_entry("0.2.0"), path)
        append_entry(_entry("0.3.0"), path)

        # Re-run the *first* version (e.g. re-generating an old release's
        # numbers under a fixed benchmark bug) with a different measurement.
        append_entry(_entry("0.1.0", overall_compression_pct=99.9), path)

        entries = load_history(path)
        assert [e.version for e in entries] == ["0.1.0", "0.2.0", "0.3.0"]
        assert entries[0].overall_compression_pct == 99.9
        # The last two entries are still the two most recent *distinct*
        # releases, not the just-replaced one — detect_regression() depends
        # on this.
        assert entries[-2].version == "0.2.0"
        assert entries[-1].version == "0.3.0"

    def test_never_loses_existing_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        append_entry(_entry("0.1.0"), path)
        append_entry(_entry("0.2.0"), path)
        append_entry(_entry("0.2.0"), path)  # re-run, should not drop 0.1.0
        assert {e.version for e in load_history(path)} == {"0.1.0", "0.2.0"}

    def test_persists_to_disk_across_process_boundary(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        append_entry(_entry("0.1.0"), path)
        # A fresh load_history() call, independent of append_entry()'s own
        # return value, must see the same data.
        assert [e.version for e in load_history(path)] == ["0.1.0"]

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "history.json"
        append_entry(_entry("0.1.0"), path)
        assert path.exists()

    def test_full_field_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        original = _entry(
            "0.5.0",
            overall_compression_pct=35.9,
            total_cases=153,
            total_tokens_saved=20549,
        )
        append_entry(original, path)
        loaded = load_history(path)[0]
        assert loaded == original


class TestRenderHistoryTable:
    def test_single_entry_has_no_delta(self) -> None:
        table = render_history_table([_entry("0.1.0", overall_compression_pct=30.0)])
        assert "v0.1.0" in table
        assert "30.0%" in table
        assert "+" not in table  # no delta line for the only entry

    def test_multiple_entries_show_delta(self) -> None:
        entries = [
            _entry("0.1.0", overall_compression_pct=30.0),
            _entry("0.2.0", overall_compression_pct=35.0),
        ]
        table = render_history_table(entries)
        assert "v0.1.0" in table
        assert "v0.2.0" in table
        assert "+5.0%" in table

    def test_empty_list_does_not_raise(self) -> None:
        assert render_history_table([]) == "\n"


class TestDetectRegression:
    def test_fewer_than_two_entries(self) -> None:
        is_regression, message = detect_regression([_entry("0.1.0")])
        assert is_regression is False
        assert "Fewer than two" in message

    def test_no_entries(self) -> None:
        is_regression, _ = detect_regression([])
        assert is_regression is False

    def test_regression_when_drop_exceeds_threshold(self) -> None:
        entries = [
            _entry("0.1.0", overall_compression_pct=40.0),
            _entry("0.2.0", overall_compression_pct=35.0),
        ]
        is_regression, message = detect_regression(
            entries, threshold_pp=DEFAULT_REGRESSION_THRESHOLD_PP
        )
        assert is_regression is True
        assert "Regression" in message
        assert "v0.1.0" in message and "v0.2.0" in message

    def test_no_regression_within_threshold(self) -> None:
        entries = [
            _entry("0.1.0", overall_compression_pct=35.0),
            _entry("0.2.0", overall_compression_pct=34.0),
        ]
        is_regression, message = detect_regression(
            entries, threshold_pp=DEFAULT_REGRESSION_THRESHOLD_PP
        )
        assert is_regression is False
        assert "No regression" in message

    def test_improvement_is_not_a_regression(self) -> None:
        entries = [
            _entry("0.1.0", overall_compression_pct=30.0),
            _entry("0.2.0", overall_compression_pct=40.0),
        ]
        is_regression, _ = detect_regression(entries)
        assert is_regression is False

    def test_custom_threshold_is_respected(self) -> None:
        entries = [
            _entry("0.1.0", overall_compression_pct=35.0),
            _entry("0.2.0", overall_compression_pct=33.5),
        ]
        # 1.5pp drop: not a regression at the 2.0pp default, but is at 1.0pp.
        assert detect_regression(entries, threshold_pp=2.0)[0] is False
        assert detect_regression(entries, threshold_pp=1.0)[0] is True

    def test_compares_last_two_entries_only(self) -> None:
        """A big historical drop further back must not trigger a regression
        if the two most recent entries are stable — detect_regression()
        only ever compares entries[-2:]."""
        entries = [
            _entry("0.1.0", overall_compression_pct=80.0),
            _entry("0.2.0", overall_compression_pct=10.0),  # huge historical drop
            _entry("0.3.0", overall_compression_pct=10.5),  # stable since then
        ]
        is_regression, _ = detect_regression(entries)
        assert is_regression is False
