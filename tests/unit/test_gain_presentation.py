"""Unit tests for quor/cli/gain_presentation.py — shared `quor gain`/
`quor dashboard` presentation logic (QB-091)."""

from __future__ import annotations

from quor.cli.gain_presentation import (
    LOW_SAMPLE_THRESHOLD,
    build_stats_table,
    build_top_filters_table,
    eligible_compression_line,
    filter_display_name,
    low_sample_caveat,
)
from quor.tracking.db import GainReport


def _report(**overrides: object) -> GainReport:
    defaults: dict[str, object] = {
        "total_invocations": 10,
        "tokens_saved": 80,
        "tokens_before": 100,
        "tokens_after": 20,
        "eligible_before": 100,
        "gross_savings": 80,
        "gross_overhead": 0,
        "negative_row_count": 0,
        "passthrough_count": 0,
        "filter_hit_rate": 1.0,
        "top_filters": [],
        "days": 30,
        "read_hook_invocations": 0,
    }
    defaults.update(overrides)
    return GainReport(**defaults)  # type: ignore[arg-type]


class TestFilterDisplayName:
    def test_translates_generic_cat_family(self) -> None:
        assert filter_display_name("cat") == "Text file read"
        assert filter_display_name("cat-python") == "Python file read"

    def test_passes_through_unrecognized_names_unchanged(self) -> None:
        """Most filters are already the real tool name a developer would
        type — pytest, eslint, docker — and must not be translated."""
        assert filter_display_name("pytest") == "pytest"
        assert filter_display_name("git-status") == "git-status"
        assert filter_display_name("some-future-filter") == "some-future-filter"


class TestLowSampleCaveat:
    def test_empty_at_or_above_threshold(self) -> None:
        report = _report(total_invocations=LOW_SAMPLE_THRESHOLD)
        assert low_sample_caveat(report) == ""

    def test_present_below_threshold(self) -> None:
        report = _report(total_invocations=LOW_SAMPLE_THRESHOLD - 1)
        caveat = low_sample_caveat(report)
        assert caveat != ""
        assert "early read" in caveat
        assert "settles as more commands run" in caveat

    def test_singular_command_wording(self) -> None:
        caveat = low_sample_caveat(_report(total_invocations=1))
        assert "1 command)" in caveat
        assert "1 commands)" not in caveat

    def test_plural_command_wording(self) -> None:
        caveat = low_sample_caveat(_report(total_invocations=2))
        assert "2 commands)" in caveat


class TestEligibleCompressionLine:
    def test_none_when_no_passthrough(self) -> None:
        report = _report(passthrough_count=0, eligible_before=100)
        assert eligible_compression_line(report) is None

    def test_none_when_eligible_before_is_zero(self) -> None:
        report = _report(passthrough_count=5, eligible_before=0)
        assert eligible_compression_line(report) is None

    def test_present_and_scoped_to_eligible_content(self) -> None:
        """The scenario that motivated QB-091: 45 commands, most
        passthrough, one big early compressible read. The eligible-only
        rate should reflect just the content a filter could touch, not the
        whole blended traffic."""
        report = _report(
            total_invocations=45,
            tokens_saved=31_600,
            tokens_before=444_500,
            eligible_before=35_400,
            passthrough_count=28,
            filter_hit_rate=17 / 45,
        )
        line = eligible_compression_line(report)
        assert line is not None
        assert "89%" in line  # 31600/35400
        assert "38%" in line  # filter_hit_rate


class TestBuildStatsTable:
    def test_includes_passthrough_row(self) -> None:
        """QB-091: `quor dashboard` previously omitted this row while
        `quor gain` showed it — the shared builder makes that drift
        impossible since both commands now call the same function."""
        table = build_stats_table(_report(passthrough_count=7))
        rendered = _plain(table)
        assert "Passthrough" in rendered
        assert "7" in rendered

    def test_includes_core_stats(self) -> None:
        rendered = _plain(build_stats_table(_report()))
        assert "Commands processed" in rendered
        assert "Filter hit rate" in rendered
        assert "Tokens before" in rendered
        assert "Tokens after" in rendered


class TestBuildTopFiltersTable:
    def test_none_when_nothing_saved(self) -> None:
        report = _report(top_filters=[("git", 0)])
        assert build_top_filters_table(report) is None

    def test_translates_filter_names(self) -> None:
        report = _report(top_filters=[("cat-python", 500)], gross_savings=500)
        rendered = _plain(build_top_filters_table(report))
        assert "Python file read" in rendered
        assert "cat-python" not in rendered

    def test_leaves_recognizable_tool_names_untranslated(self) -> None:
        report = _report(top_filters=[("pytest", 500)], gross_savings=500)
        rendered = _plain(build_top_filters_table(report))
        assert "pytest" in rendered


def _plain(renderable: object) -> str:
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    Console(file=buf, width=120, highlight=False).print(renderable)
    return buf.getvalue()
