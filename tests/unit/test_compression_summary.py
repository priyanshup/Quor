"""Unit tests for `quor explain`'s deterministic compression breakdown (QB-057).

`build_compression_summary()` is a pure function over already-collected
`StageResult` values (no pipeline execution, no I/O) — these tests construct
`StageResult` tuples directly rather than running a real filter, mirroring
how `quor.analytics.stage_stats`'s own tests would be written.
"""

from __future__ import annotations

from quor.analytics.compression_summary import build_compression_summary
from quor.pipeline.stages.base import StageResult


def _sr(
    stage_type: str,
    tokens_before: int | None,
    tokens_after: int | None,
    *,
    was_skipped: bool = False,
    error: str = "",
) -> StageResult:
    return StageResult(
        stage_type=stage_type,
        lines_before=0,
        lines_compressed=0,
        was_skipped=was_skipped,
        error=error,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )


class TestBasicBreakdown:
    def test_single_stage_with_savings(self) -> None:
        results = [_sr("strip_lines", 1000, 900)]
        summary = build_compression_summary(1000, results)

        assert summary.original_tokens == 1000
        assert summary.final_tokens == 900
        assert summary.total_saved == 100
        assert len(summary.lines) == 1
        assert summary.lines[0].stage_type == "strip_lines"
        assert summary.lines[0].tokens_saved == 100

    def test_multiple_stages_all_with_savings(self) -> None:
        results = [
            _sr("strip_lines", 1000, 900),
            _sr("group_repeated", 900, 500),
            _sr("deduplicate_consecutive", 500, 480),
        ]
        summary = build_compression_summary(1000, results)

        assert [line.stage_type for line in summary.lines] == [
            "strip_lines",
            "group_repeated",
            "deduplicate_consecutive",
        ]
        assert [line.tokens_saved for line in summary.lines] == [100, 400, 20]
        assert summary.final_tokens == 480
        assert summary.total_saved == 520

    def test_example_from_task_description(self) -> None:
        """Mirrors the task's own example numbers exactly."""
        results = [
            _sr("git_headers", 8452, 8328),  # 124 saved
            _sr("group_repeated", 8328, 7296),  # 1032 saved
            _sr("dedupe_diagnostics", 7296, 6884),  # 412 saved
            _sr("strip_lines", 6884, 6827),  # 57 saved
            _sr("max_tokens", 6827, 4827),  # 2000 saved (caps to budget)
        ]
        summary = build_compression_summary(8452, results)

        assert summary.original_tokens == 8452
        assert summary.final_tokens == 4827
        assert summary.total_saved == 3625
        assert round(summary.saved_pct, 1) == 42.9


class TestZeroSavingsOmitted:
    def test_zero_saving_stage_is_omitted(self) -> None:
        results = [
            _sr("strip_lines", 1000, 900),
            _sr("deduplicate_consecutive", 900, 900),  # nothing to dedupe
        ]
        summary = build_compression_summary(1000, results)

        assert [line.stage_type for line in summary.lines] == ["strip_lines"]
        assert summary.total_saved == 100

    def test_all_stages_zero_saving_yields_empty_breakdown(self) -> None:
        results = [
            _sr("strip_lines", 1000, 1000),
            _sr("deduplicate_consecutive", 1000, 1000),
        ]
        summary = build_compression_summary(1000, results)

        assert summary.lines == ()
        assert summary.total_saved == 0
        assert summary.final_tokens == summary.original_tokens

    def test_no_stages_at_all(self) -> None:
        summary = build_compression_summary(500, [])
        assert summary.lines == ()
        assert summary.total_saved == 0
        assert summary.final_tokens == 500


class TestSkippedAndFailedStagesContributeZero:
    def test_skipped_stage_is_ignored(self) -> None:
        results = [
            _sr("strip_lines", 1000, 900),
            _sr("group_repeated", 900, 900, was_skipped=True),
        ]
        summary = build_compression_summary(1000, results)

        assert [line.stage_type for line in summary.lines] == ["strip_lines"]
        assert summary.total_saved == 100

    def test_failed_stage_is_ignored(self) -> None:
        results = [
            _sr("strip_lines", 1000, 900),
            _sr("group_repeated", 900, 900, error="boom"),
        ]
        summary = build_compression_summary(1000, results)

        assert [line.stage_type for line in summary.lines] == ["strip_lines"]
        assert summary.total_saved == 100

    def test_untracked_stage_none_tokens_is_ignored(self) -> None:
        """A StageResult produced without track_tokens=True (tokens_before/
        after both None) must contribute zero, not raise."""
        results = [
            _sr("strip_lines", 1000, 900),
            _sr("group_repeated", None, None),
        ]
        summary = build_compression_summary(1000, results)

        assert [line.stage_type for line in summary.lines] == ["strip_lines"]
        assert summary.total_saved == 100


class TestSameStageTypeTwiceAggregates:
    def test_two_group_repeated_blocks_combine_into_one_line(self) -> None:
        """Mirrors ci.toml's gradle/maven filters, which each now have two
        separate group_repeated stages (Java stack frames + deprecation
        warnings) — both must roll up into a single reported line."""
        results = [
            _sr("group_repeated", 1000, 700),  # Java frame collapsing: 300
            _sr("strip_lines", 700, 650),
            _sr("group_repeated", 650, 620),  # deprecation warnings: 30
        ]
        summary = build_compression_summary(1000, results)

        assert [line.stage_type for line in summary.lines] == ["group_repeated", "strip_lines"]
        group_repeated_line = next(line for line in summary.lines if line.stage_type == "group_repeated")
        assert group_repeated_line.tokens_saved == 330
        assert summary.total_saved == 380


class TestInvariants:
    def test_total_saved_always_equals_sum_of_lines(self) -> None:
        results = [
            _sr("strip_lines", 5000, 4500),
            _sr("group_repeated", 4500, 3000),
            _sr("deduplicate_consecutive", 3000, 3000),
            _sr("max_tokens", 3000, 2800),
        ]
        summary = build_compression_summary(5000, results)

        assert summary.total_saved == sum(line.tokens_saved for line in summary.lines)
        assert summary.final_tokens == summary.original_tokens - summary.total_saved

    def test_deterministic_across_repeated_calls(self) -> None:
        results = [
            _sr("strip_lines", 5000, 4500),
            _sr("group_repeated", 4500, 3000),
        ]
        first = build_compression_summary(5000, results)
        second = build_compression_summary(5000, list(results))

        assert first == second

    def test_saved_pct_rounds_to_task_example(self) -> None:
        summary = build_compression_summary(8452, [_sr("strip_lines", 8452, 4827)])
        assert f"{summary.saved_pct:.1f}" == "42.9"

    def test_zero_original_tokens_never_divides_by_zero(self) -> None:
        summary = build_compression_summary(0, [])
        assert summary.saved_pct == 0.0
