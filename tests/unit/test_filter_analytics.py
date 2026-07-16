"""Unit tests for QB-054's per-filter analytics modules:
quor/analytics/filter_baseline.py, filter_divergence.py, filter_history.py,
filter_report.py.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from quor.analytics.filter_baseline import BenchmarkFilterStats, load_benchmark_filter_stats
from quor.analytics.filter_divergence import (
    LowPerformer,
    UsageDivergence,
    compute_divergence,
    flag_low_performers,
)
from quor.analytics.filter_history import (
    AnalyticsHistoryEntry,
    FilterSnapshot,
    append_snapshot,
    build_entry,
    growing_filters,
    load_history,
)
from quor.analytics.filter_report import render_filter_analytics_report
from quor.tracking.db import PASSTHROUGH_LABEL, FilterAnalyticsReport, FilterUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage(
    name: str,
    *,
    count: int = 10,
    usage_pct: float = 50.0,
    original: int = 1000,
    final: int = 500,
    compression_pct: float = 50.0,
    passthrough_pct: float = 0.0,
    duration_ms: float = 5.0,
) -> FilterUsage:
    return FilterUsage(
        filter_name=name,
        invocation_count=count,
        usage_pct=usage_pct,
        original_tokens=original,
        final_tokens=final,
        tokens_saved=original - final,
        avg_compression_pct=compression_pct,
        passthrough_pct=passthrough_pct,
        avg_duration_ms=duration_ms,
    )


def _write_baseline(tmp_path: Path, results: list[dict]) -> Path:
    path = tmp_path / "baseline.json"
    path.write_bytes(orjson.dumps({"results": results}))
    return path


# ---------------------------------------------------------------------------
# filter_baseline.py
# ---------------------------------------------------------------------------


class TestLoadBenchmarkFilterStats:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_benchmark_filter_stats(tmp_path / "missing.json") == {}

    def test_empty_results_returns_empty(self, tmp_path: Path) -> None:
        path = _write_baseline(tmp_path, [])
        assert load_benchmark_filter_stats(path) == {}

    def test_groups_by_matched_filter(self, tmp_path: Path) -> None:
        path = _write_baseline(tmp_path, [
            {"matched_filter": "git-status", "original_tokens": 100, "final_tokens": 50},
            {"matched_filter": "git-status", "original_tokens": 200, "final_tokens": 100},
            {"matched_filter": "pytest", "original_tokens": 400, "final_tokens": 300},
        ])
        stats = load_benchmark_filter_stats(path)
        assert stats["git-status"].case_count == 2
        assert stats["git-status"].usage_pct == pytest.approx(2 / 3 * 100)
        # aggregate ratio: (300 - 150) / 300 * 100
        assert stats["git-status"].compression_pct == pytest.approx(50.0)
        assert stats["pytest"].case_count == 1

    def test_cases_with_no_matched_filter_are_excluded(self, tmp_path: Path) -> None:
        path = _write_baseline(tmp_path, [
            {"matched_filter": None, "original_tokens": 100, "final_tokens": 100},
            {"matched_filter": "git-status", "original_tokens": 100, "final_tokens": 50},
        ])
        stats = load_benchmark_filter_stats(path)
        assert set(stats) == {"git-status"}
        # total_cases in the usage_pct denominator still includes the whole
        # file (2), not just the matched ones.
        assert stats["git-status"].usage_pct == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# filter_divergence.py
# ---------------------------------------------------------------------------


class TestFlagLowPerformers:
    def test_flags_negative_and_near_zero(self) -> None:
        filters = (
            _usage("good", compression_pct=60.0),
            _usage("near_zero", compression_pct=2.0),
            _usage("negative", compression_pct=-40.0),
        )
        flagged = flag_low_performers(filters)
        names = [f.filter_name for f in flagged]
        assert names == ["negative", "near_zero"]  # worst first

    def test_passthrough_bucket_is_excluded(self) -> None:
        filters = (_usage(PASSTHROUGH_LABEL, compression_pct=0.0),)
        assert flag_low_performers(filters) == []

    def test_threshold_is_configurable(self) -> None:
        filters = (_usage("borderline", compression_pct=8.0),)
        assert flag_low_performers(filters, threshold_pct=5.0) == []
        assert flag_low_performers(filters, threshold_pct=10.0) == [
            LowPerformer("borderline", 8.0, 10)
        ]


class TestComputeDivergence:
    def test_only_overlapping_filters_are_compared(self) -> None:
        filters = (
            _usage("git-status", usage_pct=40.0, compression_pct=60.0),
            _usage("only-real", usage_pct=10.0, compression_pct=20.0),
        )
        benchmark = {
            "git-status": BenchmarkFilterStats("git-status", 5, 50.0, 58.0),
            "only-benchmark": BenchmarkFilterStats("only-benchmark", 1, 5.0, 10.0),
        }
        diffs = compute_divergence(filters, benchmark)
        assert [d.filter_name for d in diffs] == ["git-status"]

    def test_deltas_are_real_minus_benchmark(self) -> None:
        filters = (_usage("mypy", usage_pct=20.0, compression_pct=-41.2),)
        benchmark = {"mypy": BenchmarkFilterStats("mypy", 3, 5.0, 46.1)}
        [d] = compute_divergence(filters, benchmark)
        assert d.compression_delta_pp == pytest.approx(-41.2 - 46.1)
        assert d.usage_delta_pp == pytest.approx(15.0)

    def test_passthrough_bucket_never_compared(self) -> None:
        filters = (_usage(PASSTHROUGH_LABEL, usage_pct=10.0, compression_pct=0.0),)
        benchmark = {PASSTHROUGH_LABEL: BenchmarkFilterStats(PASSTHROUGH_LABEL, 1, 1.0, 1.0)}
        assert compute_divergence(filters, benchmark) == []

    def test_sorted_by_largest_absolute_divergence_first(self) -> None:
        filters = (
            _usage("small-gap", compression_pct=51.0),
            _usage("big-gap", compression_pct=-10.0),
        )
        benchmark = {
            "small-gap": BenchmarkFilterStats("small-gap", 1, 0.0, 50.0),
            "big-gap": BenchmarkFilterStats("big-gap", 1, 0.0, 50.0),
        }
        diffs = compute_divergence(filters, benchmark)
        assert [d.filter_name for d in diffs] == ["big-gap", "small-gap"]


# ---------------------------------------------------------------------------
# filter_history.py
# ---------------------------------------------------------------------------


class TestBuildEntry:
    def test_rounds_and_copies_every_filter(self) -> None:
        report = FilterAnalyticsReport(
            total_invocations=10,
            days=30,
            filters=(_usage("git-status", compression_pct=33.33333),),
        )
        entry = build_entry(report)
        assert entry.total_invocations == 10
        assert entry.days == 30
        assert entry.filters[0].filter_name == "git-status"
        assert entry.filters[0].avg_compression_pct == pytest.approx(33.33)

    def test_deterministic_aside_from_recorded_at(self) -> None:
        """Two entries built from the identical report must be identical in
        every field except recorded_at (a wall-clock timestamp)."""
        report = FilterAnalyticsReport(
            total_invocations=5,
            days=7,
            filters=(_usage("pytest"),),
        )
        e1 = build_entry(report)
        e2 = build_entry(report)
        assert e1.days == e2.days
        assert e1.total_invocations == e2.total_invocations
        assert e1.filters == e2.filters


class TestHistoryPersistence:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_history(tmp_path / "missing.json") == []

    def test_append_is_strictly_additive(self, tmp_path: Path) -> None:
        """Unlike QB-051's version-keyed history, every append_snapshot call
        adds a new row — re-running never replaces a prior entry."""
        path = tmp_path / "history.json"
        report = FilterAnalyticsReport(total_invocations=1, days=30, filters=(_usage("git-status"),))

        entries = append_snapshot(build_entry(report), path)
        assert len(entries) == 1

        entries = append_snapshot(build_entry(report), path)
        assert len(entries) == 2

        entries = append_snapshot(build_entry(report), path)
        assert len(entries) == 3

    def test_append_persists_to_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        report = FilterAnalyticsReport(total_invocations=1, days=30, filters=(_usage("git-status"),))
        append_snapshot(build_entry(report), path)
        append_snapshot(build_entry(report), path)

        reloaded = load_history(path)
        assert len(reloaded) == 2
        assert all(isinstance(e, AnalyticsHistoryEntry) for e in reloaded)
        assert reloaded[0].filters[0].filter_name == "git-status"

    def test_round_trip_preserves_filter_snapshot_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        report = FilterAnalyticsReport(
            total_invocations=2,
            days=30,
            filters=(_usage("git-status", count=2, original=300, final=60),),
        )
        append_snapshot(build_entry(report), path)
        [entry] = load_history(path)
        [snap] = entry.filters
        assert snap == FilterSnapshot(
            filter_name="git-status",
            invocation_count=2,
            usage_pct=50.0,
            original_tokens=300,
            final_tokens=60,
            tokens_saved=240,
            avg_compression_pct=50.0,
            passthrough_pct=0.0,
            avg_duration_ms=5.0,
        )

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "history.json"
        report = FilterAnalyticsReport(total_invocations=1, days=30, filters=(_usage("git-status"),))
        append_snapshot(build_entry(report), path)
        assert path.exists()


class TestGrowingFilters:
    def _entry(self, usage_by_name: dict[str, float]) -> AnalyticsHistoryEntry:
        return AnalyticsHistoryEntry(
            recorded_at="2026-01-01T00:00:00+00:00",
            days=30,
            total_invocations=100,
            filters=tuple(
                FilterSnapshot(
                    filter_name=name,
                    invocation_count=1,
                    usage_pct=pct,
                    original_tokens=100,
                    final_tokens=50,
                    tokens_saved=50,
                    avg_compression_pct=50.0,
                    passthrough_pct=0.0,
                    avg_duration_ms=1.0,
                )
                for name, pct in usage_by_name.items()
            ),
        )

    def test_fewer_than_two_entries_returns_empty(self) -> None:
        assert growing_filters([]) == []
        assert growing_filters([self._entry({"git-status": 10.0})]) == []

    def test_detects_growth_between_oldest_and_newest(self) -> None:
        entries = [
            self._entry({"git-status": 10.0, "pytest": 40.0}),
            self._entry({"git-status": 25.0, "pytest": 40.0}),
        ]
        growth = growing_filters(entries)
        assert [g.filter_name for g in growth] == ["git-status"]
        assert growth[0].growth_pp == pytest.approx(15.0)

    def test_ignores_middle_snapshots_uses_oldest_and_newest_only(self) -> None:
        entries = [
            self._entry({"git-status": 10.0}),
            self._entry({"git-status": 90.0}),  # noisy spike, ignored
            self._entry({"git-status": 20.0}),
        ]
        growth = growing_filters(entries)
        assert growth[0].growth_pp == pytest.approx(10.0)

    def test_filter_only_in_one_snapshot_is_skipped(self) -> None:
        entries = [
            self._entry({"git-status": 10.0}),
            self._entry({"pytest": 20.0}),
        ]
        assert growing_filters(entries) == []

    def test_passthrough_bucket_excluded(self) -> None:
        """The `(no filter matched)` bucket isn't a filter — it must never
        show up as a "growing filter", the same exclusion every other
        report section already applies (see TestFlagLowPerformers/
        TestComputeDivergence above)."""
        entries = [
            self._entry({PASSTHROUGH_LABEL: 10.0, "git-status": 5.0}),
            self._entry({PASSTHROUGH_LABEL: 40.0, "git-status": 20.0}),
        ]
        growth = growing_filters(entries)
        assert [g.filter_name for g in growth] == ["git-status"]

    def test_sorted_largest_growth_first(self) -> None:
        entries = [
            self._entry({"a": 10.0, "b": 10.0}),
            self._entry({"a": 15.0, "b": 40.0}),
        ]
        growth = growing_filters(entries)
        assert [g.filter_name for g in growth] == ["b", "a"]


# ---------------------------------------------------------------------------
# filter_report.py
# ---------------------------------------------------------------------------


class TestRenderFilterAnalyticsReport:
    def test_renders_all_required_sections(self) -> None:
        report = FilterAnalyticsReport(
            total_invocations=3,
            days=30,
            filters=(
                _usage("git-status", count=2, usage_pct=66.7, compression_pct=60.0),
                _usage("mypy", count=1, usage_pct=33.3, compression_pct=-10.0),
            ),
        )
        text = render_filter_analytics_report(report, history=[], benchmark={})
        assert "Top" in text and "most-used filters" in text
        assert "Top compression performers" in text
        assert "Worst compression performers" in text
        assert "Negative or near-zero compression" in text
        assert "Real usage vs benchmark divergence" in text
        assert "Filters growing over time" in text
        # A flagged low performer shows up by name.
        assert "mypy" in text

    def test_empty_report_does_not_raise(self) -> None:
        report = FilterAnalyticsReport(total_invocations=0, days=30, filters=())
        text = render_filter_analytics_report(report, history=[], benchmark={})
        assert isinstance(text, str)
        assert text.endswith("\n")
