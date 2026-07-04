"""Phase 2 unit tests: all five built-in compression stages.

Each stage is tested for:
  - Empty input (no crash)
  - No matching lines → all KEEP unchanged
  - All matching lines → all COMPRESS
  - PROTECT lines survive regardless of matching
  - Stage-specific behaviour (group_repeated count, max_tokens strategies, etc.)
  - Timeout handling (catastrophic backtracking pattern + mocked _search)
"""

from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from quor.pipeline.engine import Pipeline, StageEntry
from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages import _utils
from quor.pipeline.stages.deduplicate_consecutive import (
    DeduplicateConsecutiveConfig,
    DeduplicateConsecutiveStage,
)
from quor.pipeline.stages.group_repeated import GroupRepeatedConfig, GroupRepeatedStage
from quor.pipeline.stages.match_output import MatchOutputConfig, MatchOutputStage
from quor.pipeline.stages.max_tokens import MaxTokensConfig, MaxTokensStage
from quor.pipeline.stages.python_ast_summarize import (
    PythonAstSummarizeConfig,
    PythonAstSummarizeStage,
)
from quor.pipeline.stages.regex_replace import (
    RegexReplaceConfig,
    RegexReplaceRule,
    RegexReplaceStage,
)
from quor.pipeline.stages.remove_ansi import RemoveAnsiConfig, RemoveAnsiStage
from quor.pipeline.stages.strip_lines import StripLinesConfig, StripLinesStage
from quor.pipeline.stages.truncate_lines import TruncateLinesConfig, TruncateLinesStage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _protect(line: str) -> LineMask:
    return LineMask(line=line, decision=Decision.PROTECT, reason="test", stage="test")


def _compress(line: str) -> LineMask:
    return LineMask(line=line, decision=Decision.COMPRESS, reason="test", stage="test")


# ---------------------------------------------------------------------------
# remove_ansi
# ---------------------------------------------------------------------------

class TestRemoveAnsi:
    stage = RemoveAnsiStage()

    def _config(self, preserve: list[str] | None = None) -> RemoveAnsiConfig:
        return RemoveAnsiConfig(type="remove_ansi", preserve_patterns=preserve or [])

    def test_empty_input(self) -> None:
        mask = ContentMask.from_text("")
        result = self.stage.apply(mask, self._config())
        assert result.render() == ""

    def test_no_ansi_lines_unchanged(self) -> None:
        mask = ContentMask.from_text("hello\nworld")
        result = self.stage.apply(mask, self._config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_ansi_only_line_compressed(self) -> None:
        ansi_line = "\x1b[32m\x1b[0m"
        mask = ContentMask.from_text(ansi_line)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.COMPRESS

    def test_ansi_with_text_kept(self) -> None:
        mixed = "\x1b[32mPASSED\x1b[0m tests/test_foo.py"
        mask = ContentMask.from_text(mixed)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP

    def test_all_ansi_lines_compressed(self) -> None:
        lines = "\n".join(["\x1b[0m", "\x1b[K", "\x1b[32m\x1b[0m"])
        mask = ContentMask.from_text(lines)
        result = self.stage.apply(mask, self._config())
        assert all(lm.decision is Decision.COMPRESS for lm in result.lines)

    def test_protect_line_not_compressed(self) -> None:
        lm_protect = _protect("\x1b[0m")  # ANSI-only but PROTECT
        mask = ContentMask(lines=(lm_protect,))
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.PROTECT

    def test_preserve_pattern_creates_protect(self) -> None:
        ansi_line = "\x1b[0m"  # would be COMPRESS without preserve
        mask = ContentMask.from_text(ansi_line)
        config = self._config(preserve=[r"\x1b\[0m"])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT

    def test_wrong_config_type_raises(self) -> None:
        mask = ContentMask.from_text("x")
        bad_config = StripLinesConfig(type="strip_lines")
        with pytest.raises(TypeError, match="RemoveAnsiConfig"):
            self.stage.apply(mask, bad_config)


# ---------------------------------------------------------------------------
# strip_lines
# ---------------------------------------------------------------------------

class TestStripLines:
    stage = StripLinesStage()

    def _config(
        self,
        patterns: list[str] | None = None,
        preserve: list[str] | None = None,
    ) -> StripLinesConfig:
        return StripLinesConfig(
            type="strip_lines",
            patterns=patterns or [],
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        mask = ContentMask.from_text("")
        result = self.stage.apply(mask, self._config(patterns=[r"^PASSED"]))
        assert result.render() == ""

    def test_no_matching_lines_unchanged(self) -> None:
        mask = ContentMask.from_text("FAILED test\nERROR: oops")
        result = self.stage.apply(mask, self._config(patterns=[r"^PASSED"]))
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_all_matching_lines_compressed(self) -> None:
        mask = ContentMask.from_text("PASSED test_a\nPASSED test_b\nPASSED test_c")
        result = self.stage.apply(mask, self._config(patterns=[r"^PASSED"]))
        assert all(lm.decision is Decision.COMPRESS for lm in result.lines)

    def test_mixed_matching(self) -> None:
        mask = ContentMask.from_text("PASSED test_a\nFAILED test_b\nPASSED test_c")
        result = self.stage.apply(mask, self._config(patterns=[r"^PASSED"]))
        decisions = [lm.decision for lm in result.lines]
        assert decisions == [Decision.COMPRESS, Decision.KEEP, Decision.COMPRESS]

    def test_protect_line_not_compressed(self) -> None:
        lm = _protect("PASSED test_a")
        mask = ContentMask(lines=(lm,))
        result = self.stage.apply(mask, self._config(patterns=[r"^PASSED"]))
        assert result.lines[0].decision is Decision.PROTECT

    def test_preserve_pattern_overrides_strip_pattern(self) -> None:
        """A line matching both strip and preserve patterns should be PROTECT, not COMPRESS."""
        mask = ContentMask.from_text("FAILED AssertionError: expected True")
        config = self._config(patterns=[r"FAILED"], preserve=[r"AssertionError"])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT

    def test_preserve_only_creates_protect_on_non_strip_lines(self) -> None:
        mask = ContentMask.from_text("KEEP_THIS\nDROP_THIS")
        config = self._config(patterns=[r"^DROP"], preserve=[r"^KEEP"])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[1].decision is Decision.COMPRESS

    def test_already_compressed_line_not_re_compressed(self) -> None:
        lm = _compress("PASSED test_a")
        mask = ContentMask(lines=(lm,))
        result = self.stage.apply(mask, self._config(patterns=[r"^PASSED"]))
        assert result.lines[0].decision is Decision.COMPRESS
        assert result.lines[0].stage == "test"  # unchanged: stage not updated

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="StripLinesConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))

    def test_timeout_warns_and_leaves_line_unchanged(self) -> None:
        config = self._config(patterns=[r".*"])
        mask = ContentMask.from_text("any content")

        with (
            patch.object(_utils, "_search", side_effect=TimeoutError("timed out")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = self.stage.apply(mask, config)

        assert result.lines[0].decision is Decision.KEEP
        assert any("timed out" in str(w.message).lower() for w in caught)


# ---------------------------------------------------------------------------
# deduplicate_consecutive
# ---------------------------------------------------------------------------

class TestDeduplicateConsecutive:
    stage = DeduplicateConsecutiveStage()

    def _config(self, preserve: list[str] | None = None) -> DeduplicateConsecutiveConfig:
        return DeduplicateConsecutiveConfig(
            type="deduplicate_consecutive", preserve_patterns=preserve or []
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_no_duplicates_unchanged(self) -> None:
        mask = ContentMask.from_text("a\nb\nc")
        result = self.stage.apply(mask, self._config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_consecutive_duplicate_compressed(self) -> None:
        mask = ContentMask.from_text("same\nsame\nsame")
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.COMPRESS

    def test_non_consecutive_duplicates_kept(self) -> None:
        mask = ContentMask.from_text("a\nb\na")
        result = self.stage.apply(mask, self._config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_already_compressed_line_not_tracked(self) -> None:
        """An already-COMPRESS line does not break the duplicate chain."""
        lines = (
            LineMask(line="same", decision=Decision.KEEP),
            _compress("noise"),
            LineMask(line="same", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        # First "same" is KEEP; middle noise is COMPRESS (passthrough); third "same" IS a dup
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.COMPRESS

    def test_protect_line_not_compressed(self) -> None:
        lines = (
            LineMask(line="x", decision=Decision.KEEP),
            _protect("x"),  # same content but PROTECT
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.PROTECT

    def test_protect_line_updates_last_kept(self) -> None:
        """A PROTECT line should update the 'last kept' tracker."""
        lines = (
            _protect("anchor"),
            LineMask(line="anchor", decision=Decision.KEEP),  # dup of PROTECT line
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.COMPRESS

    def test_preserve_pattern_creates_protect(self) -> None:
        mask = ContentMask.from_text("ERROR: bad\nnormal line")
        config = self._config(preserve=[r"^ERROR"])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="DeduplicateConsecutiveConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))


# ---------------------------------------------------------------------------
# group_repeated
# ---------------------------------------------------------------------------

class TestGroupRepeated:
    stage = GroupRepeatedStage()

    def _config(
        self,
        patterns: list[str] | None = None,
        min_count: int = 2,
        preserve: list[str] | None = None,
    ) -> GroupRepeatedConfig:
        return GroupRepeatedConfig(
            type="group_repeated",
            patterns=patterns or [],
            min_count=min_count,
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_no_patterns_is_noop(self) -> None:
        mask = ContentMask.from_text("a\na\na")
        result = self.stage.apply(mask, self._config(patterns=[]))
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_single_occurrence_not_collapsed(self) -> None:
        mask = ContentMask.from_text("WARNING: foo\nother line")
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=2))
        assert result.lines[0].decision is Decision.KEEP
        assert "(×" not in result.lines[0].line  # noqa: RUF001

    def test_two_occurrences_collapsed_with_min_count_2(self) -> None:
        mask = ContentMask.from_text("WARNING: foo\nWARNING: foo")
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=2))
        assert "×2" in result.lines[0].line  # noqa: RUF001
        assert result.lines[1].decision is Decision.COMPRESS

    def test_min_count_boundary_one_below_threshold_not_collapsed(self) -> None:
        """min_count=3 with exactly 2 occurrences must NOT collapse — the run
        length must be strictly >= min_count, not off-by-one either way."""
        mask = ContentMask.from_text("WARNING: foo\nWARNING: foo")
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=3))
        assert all(lm.decision is Decision.KEEP for lm in result.lines)
        assert "(×" not in result.lines[0].line  # noqa: RUF001

    def test_min_count_boundary_exact_threshold_collapsed(self) -> None:
        """min_count=3 with exactly 3 occurrences must collapse — the other
        side of the same boundary as the test above."""
        mask = ContentMask.from_text("WARNING: foo\nWARNING: foo\nWARNING: foo")
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=3))
        assert "×3" in result.lines[0].line  # noqa: RUF001
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.COMPRESS

    def test_five_occurrences_suffix(self) -> None:
        text = "\n".join(["WARNING: disk low"] * 5)
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=2))
        assert "×5" in result.lines[0].line  # noqa: RUF001
        assert result.lines[0].decision is Decision.KEEP
        for lm in result.lines[1:]:
            assert lm.decision is Decision.COMPRESS

    def test_exact_count_in_suffix(self) -> None:
        n = 7
        text = "\n".join(["INFO: loop"] * n)
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config(patterns=["^INFO:"], min_count=2))
        assert f"×{n}" in result.lines[0].line  # noqa: RUF001

    def test_protect_line_breaks_run(self) -> None:
        lines = (
            LineMask(line="WARNING: foo", decision=Decision.KEEP),
            LineMask(line="WARNING: foo", decision=Decision.KEEP),
            _protect("WARNING: foo"),  # PROTECT breaks the run
            LineMask(line="WARNING: foo", decision=Decision.KEEP),
            LineMask(line="WARNING: foo", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=2))
        assert result.lines[2].decision is Decision.PROTECT
        assert "×2" in result.lines[0].line  # noqa: RUF001
        assert "×2" in result.lines[3].line  # noqa: RUF001

    def test_protect_lines_not_modified(self) -> None:
        lm = _protect("WARNING: critical")
        mask = ContentMask(lines=(lm,))
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=2))
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].line == "WARNING: critical"

    def test_multiple_distinct_runs(self) -> None:
        text = "WARNING: a\nWARNING: a\nINFO: b\nWARNING: a\nWARNING: a"
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=2))
        # First run: lines 0-1 collapsed
        assert "×2" in result.lines[0].line  # noqa: RUF001
        assert result.lines[1].decision is Decision.COMPRESS
        # INFO line untouched
        assert result.lines[2].decision is Decision.KEEP
        # Second run: lines 3-4 collapsed
        assert "×2" in result.lines[3].line  # noqa: RUF001
        assert result.lines[4].decision is Decision.COMPRESS

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="GroupRepeatedConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))

    def test_timeout_warns_and_leaves_line_unchanged(self) -> None:
        from quor.pipeline.stages import group_repeated as _gr_mod

        config = self._config(patterns=["^WARNING:"], min_count=2)
        mask = ContentMask.from_text("WARNING: foo\nWARNING: foo")

        # group_repeated imports _search by name, so patch it in its own module namespace
        with (
            patch.object(_gr_mod, "_search", side_effect=TimeoutError("timed out")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = self.stage.apply(mask, config)

        assert any("timed out" in str(w.message).lower() for w in caught)
        # No lines should be compressed on timeout
        for lm in result.lines:
            assert lm.decision is not Decision.COMPRESS


# ---------------------------------------------------------------------------
# max_tokens
# ---------------------------------------------------------------------------

class TestMaxTokens:
    stage = MaxTokensStage()

    def _config(
        self,
        limit: int = 1000,
        strategy: str = "tail",
        preserve: list[str] | None = None,
    ) -> MaxTokensConfig:
        from typing import Literal
        s: Literal["head", "tail", "both"] = strategy  # type: ignore[assignment]
        return MaxTokensConfig(
            type="max_tokens",
            limit=limit,
            strategy=s,
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config(limit=10))
        assert result.render() == ""

    def test_within_budget_unchanged(self) -> None:
        mask = ContentMask.from_text("short line")
        result = self.stage.apply(mask, self._config(limit=1000))
        assert result.lines[0].decision is Decision.KEEP

    def test_head_strategy_keeps_first_lines(self) -> None:
        # 5 lines, each ~100 chars = 25 tokens. limit=50 keeps first 2.
        lines = "\n".join(["a" * 100] * 5)
        mask = ContentMask.from_text(lines)
        result = self.stage.apply(mask, self._config(limit=50, strategy="head"))
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.KEEP
        assert result.lines[2].decision is Decision.COMPRESS
        assert result.lines[3].decision is Decision.COMPRESS
        assert result.lines[4].decision is Decision.COMPRESS

    def test_tail_strategy_keeps_last_lines(self) -> None:
        lines = "\n".join(["a" * 100] * 5)
        mask = ContentMask.from_text(lines)
        result = self.stage.apply(mask, self._config(limit=50, strategy="tail"))
        assert result.lines[0].decision is Decision.COMPRESS
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.COMPRESS
        assert result.lines[3].decision is Decision.KEEP
        assert result.lines[4].decision is Decision.KEEP

    def test_both_strategy_keeps_head_and_tail(self) -> None:
        lines = "\n".join(["a" * 100] * 6)  # 6 lines, 25 tok each; limit=50 → 25/side → 1/side
        mask = ContentMask.from_text(lines)
        result = self.stage.apply(mask, self._config(limit=50, strategy="both"))
        assert result.lines[0].decision is Decision.KEEP   # head
        assert result.lines[5].decision is Decision.KEEP   # tail
        # Middle may be compressed
        for lm in result.lines[2:4]:
            assert lm.decision is Decision.COMPRESS

    def test_protect_lines_never_compressed(self) -> None:
        lines = (
            _protect("critical error line"),
            LineMask(line="a" * 500, decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(limit=1, strategy="tail"))
        assert result.lines[0].decision is Decision.PROTECT

    def test_keep_line_compressed_when_budget_too_tight(self) -> None:
        """PROTECT always survives; KEEP line whose cost exceeds budget gets compressed."""
        # KEEP line costs 25 tokens. limit=1 → not enough budget → COMPRESS.
        lines = (
            _protect("a" * 100),
            LineMask(line="b" * 100, decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(limit=1, strategy="tail"))
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[1].decision is Decision.COMPRESS

    def test_already_compressed_line_passthrough(self) -> None:
        lm = _compress("long " * 100)
        mask = ContentMask(lines=(lm,))
        result = self.stage.apply(mask, self._config(limit=1, strategy="tail"))
        assert result.lines[0].decision is Decision.COMPRESS

    def test_preserve_pattern_creates_protect(self) -> None:
        mask = ContentMask.from_text("ERROR: critical\nnormal line\nnormal line")
        config = self._config(limit=1, preserve=["^ERROR"])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="MaxTokensConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))

    # -- ADR-031 / QB-012: best-effort budget regression guards --------------
    # These lock in the *decided, observable* semantics: max_tokens is a
    # target, never a hard guarantee, and PROTECT content pushing the
    # rendered output over the configured limit is correct, not a bug. If a
    # future change made this a hard budget (compressing PROTECT to fit),
    # these tests would fail.

    def test_rendered_output_exceeds_limit_when_protect_heavy(self) -> None:
        """ADR-031's core claim, asserted end-to-end on rendered output size,
        not just per-line decisions: when PROTECT content alone exceeds the
        configured limit, the stage must not compress it to comply — the
        final render is allowed to exceed `limit`."""
        # 50 PROTECT lines of 100 chars each ~= 1250 estimated tokens, well
        # over a limit of 100.
        protect_lines = tuple(
            LineMask(line="ERROR: " + ("x" * 100), decision=Decision.PROTECT, reason="preserved")
            for _ in range(50)
        )
        mask = ContentMask(lines=protect_lines)
        result = self.stage.apply(mask, self._config(limit=100, strategy="tail"))

        assert all(lm.decision is Decision.PROTECT for lm in result.lines)
        rendered_tokens = len(result.render()) // 4
        assert rendered_tokens > 100, (
            "best-effort budget must not compress PROTECT content to fit — "
            "rendered output should exceed the configured limit here"
        )

    def test_keep_lines_still_compressed_around_oversized_protect_block(self) -> None:
        """Best-effort applies only to PROTECT; ordinary KEEP lines around an
        oversized PROTECT block are still compressed as normal."""
        lines = (
            LineMask(line="ERROR: " + ("x" * 500), decision=Decision.PROTECT),
            LineMask(line="noise " * 50, decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(limit=10, strategy="tail"))
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[1].decision is Decision.COMPRESS

    def test_limit_zero_rejected(self) -> None:
        """MaxTokensConfig.limit has gt=0 — a zero budget is a config error,
        not a silently-accepted "compress everything" degenerate case."""
        with pytest.raises(ValidationError):
            self._config(limit=0)

    def test_limit_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._config(limit=-5)


# ---------------------------------------------------------------------------
# Cross-stage: preserve_patterns in base config
# ---------------------------------------------------------------------------

class TestPreservePatternsAcrossStages:
    def test_strip_lines_preserve_beats_strip(self) -> None:
        config = StripLinesConfig(
            type="strip_lines",
            patterns=[".*"],            # would strip everything
            preserve_patterns=["ERROR"],
        )
        mask = ContentMask.from_text("normal line\nERROR: something failed")
        result = StripLinesStage().apply(mask, config)
        assert result.lines[0].decision is Decision.COMPRESS
        assert result.lines[1].decision is Decision.PROTECT

    def test_group_repeated_preserve_not_grouped(self) -> None:
        config = GroupRepeatedConfig(
            type="group_repeated",
            patterns=["^WARNING:"],
            min_count=2,
            preserve_patterns=["^WARNING: critical"],
        )
        lines = (
            LineMask(line="WARNING: critical", decision=Decision.KEEP),
            LineMask(line="WARNING: critical", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = GroupRepeatedStage().apply(mask, config)
        # Both lines should be PROTECT, neither grouped
        for lm in result.lines:
            assert lm.decision is Decision.PROTECT


# ---------------------------------------------------------------------------
# QB-014 regression: group_repeated vs strip_lines ordering (PROTECT
# run-breaker interaction). Deliberately independent of build.toml's mypy
# filter — this locks in the *general* principle via the real Pipeline
# engine and synthetic stages, so the coverage survives even if build.toml
# is edited or removed later.
# ---------------------------------------------------------------------------


class TestGroupRepeatedStripLinesOrdering:
    _INPUT = "error: boom\n" * 3 + "note: unrelated\n"

    def _pipeline(self, *stage_order: str) -> Pipeline:
        entries = []
        for stage_type in stage_order:
            if stage_type == "group_repeated":
                entries.append(
                    StageEntry(
                        handler=GroupRepeatedStage(),
                        config=GroupRepeatedConfig(
                            type="group_repeated", patterns=["^error: "], min_count=3
                        ),
                    )
                )
            else:
                entries.append(
                    StageEntry(
                        handler=StripLinesStage(),
                        config=StripLinesConfig(
                            type="strip_lines", preserve_patterns=["error:", "note:"]
                        ),
                    )
                )
        return Pipeline(entries)

    def test_strip_lines_before_group_repeated_is_a_noop(self) -> None:
        """The pre-QB-014 (buggy) order: strip_lines' preserve_patterns marks
        every "error:" line PROTECT before group_repeated ever runs, and
        group_repeated treats PROTECT as a run-breaker — so nothing collapses.
        This test documents the bug's mechanism; it must keep failing to
        collapse in this order, since that's what QB-014's fix moved away from.
        """
        mask = ContentMask.from_text(self._INPUT)
        result = self._pipeline("strip_lines", "group_repeated").execute(mask).mask
        assert "(×3)" not in result.render()  # noqa: RUF001
        # All three "error:" lines survive individually, ungrouped, as PROTECT
        error_lines = [lm for lm in result.lines if lm.line == "error: boom"]
        assert len(error_lines) == 3
        assert all(lm.decision is Decision.PROTECT for lm in error_lines)

    def test_group_repeated_before_strip_lines_collapses_correctly(self) -> None:
        """QB-014's fix: group_repeated runs first, while lines are still
        plain KEEP, so it can collapse them. strip_lines then must not
        resurrect the compressed duplicates via preserve_patterns — the
        COMPRESS-skip guard added in the QB-014 fix is what prevents that."""
        mask = ContentMask.from_text(self._INPUT)
        result = self._pipeline("group_repeated", "strip_lines").execute(mask).mask
        rendered = result.render()
        assert "(×3)" in rendered  # noqa: RUF001
        assert "note: unrelated" in rendered
        # Exactly one visible "error:" line (the collapsed summary) — the two
        # duplicates must stay COMPRESS, not be resurrected as PROTECT.
        assert rendered.count("error: boom") == 1


# ---------------------------------------------------------------------------
# truncate_lines (QB-009)
# ---------------------------------------------------------------------------

class TestTruncateLines:
    stage = TruncateLinesStage()

    def _config(
        self,
        max_length: int = 20,
        marker: str = "…[truncated]",
        preserve: list[str] | None = None,
    ) -> TruncateLinesConfig:
        return TruncateLinesConfig(
            type="truncate_lines",
            max_length=max_length,
            marker=marker,
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_short_line_unchanged(self) -> None:
        mask = ContentMask.from_text("short")
        result = self.stage.apply(mask, self._config(max_length=20))
        assert result.lines[0].line == "short"
        assert result.lines[0].decision is Decision.KEEP

    def test_long_line_truncated_with_marker(self) -> None:
        mask = ContentMask.from_text("a" * 100)
        result = self.stage.apply(mask, self._config(max_length=20, marker="…[truncated]"))
        assert len(result.lines[0].line) == 20
        assert result.lines[0].line.endswith("…[truncated]")
        assert result.lines[0].decision is Decision.KEEP

    def test_line_count_never_changes(self) -> None:
        mask = ContentMask.from_text("short\n" + "x" * 200 + "\nshort again")
        result = self.stage.apply(mask, self._config(max_length=10))
        assert len(result.lines) == len(mask.lines) == 3

    def test_protect_line_never_truncated(self) -> None:
        lm = _protect("x" * 500)
        mask = ContentMask(lines=(lm,))
        result = self.stage.apply(mask, self._config(max_length=10))
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].line == "x" * 500

    def test_preserve_pattern_creates_protect_and_skips_truncation(self) -> None:
        mask = ContentMask.from_text("CRITICAL: " + "x" * 200)
        config = self._config(max_length=10, preserve=["^CRITICAL"])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT
        assert len(result.lines[0].line) > 10

    def test_already_compressed_line_passthrough(self) -> None:
        lm = _compress("x" * 500)
        mask = ContentMask(lines=(lm,))
        result = self.stage.apply(mask, self._config(max_length=10))
        assert result.lines[0].decision is Decision.COMPRESS
        assert result.lines[0].line == "x" * 500

    def test_marker_longer_than_max_length_falls_back_to_hard_cut(self) -> None:
        mask = ContentMask.from_text("a" * 100)
        result = self.stage.apply(mask, self._config(max_length=5, marker="…[a long marker]"))
        assert result.lines[0].line == "aaaaa"
        assert len(result.lines[0].line) == 5

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="TruncateLinesConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))


# ---------------------------------------------------------------------------
# regex_replace (QB-008)
# ---------------------------------------------------------------------------

class TestRegexReplace:
    stage = RegexReplaceStage()

    def _config(
        self,
        rules: list[tuple[str, str]] | None = None,
        preserve: list[str] | None = None,
    ) -> RegexReplaceConfig:
        return RegexReplaceConfig(
            type="regex_replace",
            rules=[RegexReplaceRule(pattern=p, replacement=r) for p, r in (rules or [])],
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config(rules=[(r"x", "y")]))
        assert result.render() == ""

    def test_no_rules_returns_mask_unchanged(self) -> None:
        mask = ContentMask.from_text("hello world")
        result = self.stage.apply(mask, self._config(rules=[]))
        assert result is mask

    def test_single_rule_substitution(self) -> None:
        mask = ContentMask.from_text("request id=123e4567-e89b-12d3-a456-426614174000 ok")
        config = self._config(
            rules=[
                (r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>"),
            ]
        )
        result = self.stage.apply(mask, config)
        assert result.lines[0].line == "request id=<uuid> ok"
        assert result.lines[0].decision is Decision.KEEP

    def test_multiple_rules_applied_in_order(self) -> None:
        mask = ContentMask.from_text("2026-07-05T12:00:00Z host-abc123 event")
        config = self._config(
            rules=[
                (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "<timestamp>"),
                (r"host-[a-z0-9]+", "<host>"),
            ]
        )
        result = self.stage.apply(mask, config)
        assert result.lines[0].line == "<timestamp> <host> event"

    def test_capture_group_backreference(self) -> None:
        mask = ContentMask.from_text("name=Alice age=30")
        config = self._config(rules=[(r"name=(\w+)", r"user=\1")])
        result = self.stage.apply(mask, config)
        assert result.lines[0].line == "user=Alice age=30"

    def test_no_match_leaves_line_unchanged(self) -> None:
        mask = ContentMask.from_text("nothing to replace here")
        config = self._config(rules=[(r"UUID-\d+", "<id>")])
        result = self.stage.apply(mask, config)
        assert result.lines[0] is mask.lines[0]

    def test_protect_line_never_modified(self) -> None:
        lm = _protect("id=123e4567-e89b-12d3-a456-426614174000")
        mask = ContentMask(lines=(lm,))
        config = self._config(rules=[(r"[0-9a-f-]{36}", "<uuid>")])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].line == lm.line

    def test_preserve_pattern_creates_protect_and_skips_substitution(self) -> None:
        mask = ContentMask.from_text("CRITICAL id=123e4567-e89b-12d3-a456-426614174000")
        config = self._config(
            rules=[(r"[0-9a-f-]{36}", "<uuid>")],
            preserve=["^CRITICAL"],
        )
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT
        assert "123e4567" in result.lines[0].line

    def test_already_compressed_line_passthrough(self) -> None:
        lm = _compress("id=123e4567-e89b-12d3-a456-426614174000")
        mask = ContentMask(lines=(lm,))
        config = self._config(rules=[(r"[0-9a-f-]{36}", "<uuid>")])
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.COMPRESS
        assert result.lines[0].line == lm.line

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="RegexReplaceConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))

    def test_timeout_warns_and_skips_rule(self) -> None:
        from quor.pipeline.stages import regex_replace as _rr_mod

        config = self._config(rules=[(r".*", "y")])
        mask = ContentMask.from_text("any content")

        with (
            patch.object(_rr_mod, "_sub", side_effect=TimeoutError("timed out")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = self.stage.apply(mask, config)

        assert result.lines[0].line == "any content"
        assert any("timed out" in str(w.message).lower() for w in caught)


# ---------------------------------------------------------------------------
# match_output (QB-010)
# ---------------------------------------------------------------------------

class TestMatchOutput:
    stage = MatchOutputStage()

    def _config(self, pattern: str, summary: str = "OK") -> MatchOutputConfig:
        return MatchOutputConfig(type="match_output", pattern=pattern, summary=summary)

    def test_empty_input_no_match_unchanged(self) -> None:
        # ContentMask.from_text("") yields one empty-string KEEP line, not zero
        # lines — a pattern that doesn't match empty content must leave it as-is.
        result = self.stage.apply(ContentMask.from_text(""), self._config(pattern=r"nonmatching"))
        assert result.render() == ""

    def test_empty_input_matching_pattern_fires(self) -> None:
        # `.*` legitimately matches empty output too — firing here is correct,
        # not a bug (this is what distinguishes match_output from a no-op).
        result = self.stage.apply(ContentMask.from_text(""), self._config(pattern=r".*"))
        assert result.render() == "OK"

    def test_full_match_collapses_to_summary(self) -> None:
        mask = ContentMask.from_text("nothing to commit, working tree clean")
        config = self._config(
            pattern=r"nothing to commit, working tree clean", summary="clean working tree"
        )
        result = self.stage.apply(mask, config)
        assert result.lines[0].line == "clean working tree"
        assert result.lines[0].decision is Decision.KEEP

    def test_line_count_never_changes_on_fire(self) -> None:
        mask = ContentMask.from_text("line one\nline two\nline three")
        config = self._config(pattern=r"line one\nline two\nline three")
        result = self.stage.apply(mask, config)
        assert len(result.lines) == len(mask.lines) == 3
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.COMPRESS

    def test_partial_match_does_not_fire(self) -> None:
        mask = ContentMask.from_text("nothing to commit, working tree clean\nextra line")
        config = self._config(pattern=r"nothing to commit, working tree clean")
        result = self.stage.apply(mask, config)
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_no_match_leaves_mask_unchanged(self) -> None:
        mask = ContentMask.from_text("some unrelated output")
        config = self._config(pattern=r"completely different pattern")
        result = self.stage.apply(mask, config)
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[0].line == "some unrelated output"

    def test_protect_line_present_prevents_firing(self) -> None:
        lm = _protect("clean output")
        mask = ContentMask(lines=(lm,))
        config = self._config(pattern=r"clean output")
        result = self.stage.apply(mask, config)
        # Would otherwise fullmatch and fire, but PROTECT presence blocks it.
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].line == "clean output"

    def test_fire_emits_observable_warning(self) -> None:
        mask = ContentMask.from_text("clean output")
        config = self._config(pattern=r"clean output")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.stage.apply(mask, config)
        assert any("match_output" in str(w.message) for w in caught)

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="MatchOutputConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))

    def test_timeout_warns_and_leaves_mask_unchanged(self) -> None:
        from quor.pipeline.stages import match_output as _mo_mod

        config = self._config(pattern=r".*")
        mask = ContentMask.from_text("any content")

        with (
            patch.object(_mo_mod, "_fullmatch", side_effect=TimeoutError("timed out")),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = self.stage.apply(mask, config)

        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[0].line == "any content"
        assert any("timed out" in str(w.message).lower() for w in caught)


# ---------------------------------------------------------------------------
# python_ast_summarize (QB-005)
# ---------------------------------------------------------------------------


class TestPythonAstSummarize:
    stage = PythonAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> PythonAstSummarizeConfig:
        return PythonAstSummarizeConfig(
            type="python_ast_summarize",
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="PythonAstSummarizeConfig"):
            self.stage.apply(ContentMask.from_text("x = 1"), RemoveAnsiConfig(type="remove_ansi"))

    def test_valid_file_compresses_body_keeps_signature_and_docstring(self) -> None:
        source = (
            "def add(x, y):\n"
            '    """Add two numbers."""\n'
            "    total = x + y\n"
            "    return total\n"
        )
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP  # def add(x, y):
        assert result.lines[1].decision is Decision.KEEP  # docstring
        assert result.lines[2].decision is Decision.COMPRESS  # total = x + y
        assert result.lines[3].decision is Decision.COMPRESS  # return total
        assert result.lines[4].decision is Decision.KEEP  # trailing blank

    def test_imports_and_module_constants_never_touched(self) -> None:
        source = "import os\n\nDEFAULT_TIMEOUT = 30\n\n\ndef run():\n    do_work()\n    return True\n"
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP  # import os
        assert result.lines[2].decision is Decision.KEEP  # DEFAULT_TIMEOUT = 30
        assert result.lines[6].decision is Decision.COMPRESS  # do_work()
        assert result.lines[7].decision is Decision.COMPRESS  # return True

    def test_syntax_error_propagates_for_engine_fail_open(self) -> None:
        """apply() deliberately does not catch parse failures itself — the
        engine's existing per-stage fail-open (Pipeline.execute) is what
        keeps the original content on a real syntax error; see the
        cat-python.toml inline test for the end-to-end behaviour."""
        mask = ContentMask.from_text("def broken(:\n    pass\n")
        with pytest.raises(SyntaxError):
            self.stage.apply(mask, self._config())

    def test_syntax_error_via_pipeline_fails_open_to_original(self) -> None:
        source = "def broken(:\n    pass\n"
        mask = ContentMask.from_text(source)
        entry = StageEntry(handler=self.stage, config=self._config())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = Pipeline([entry]).execute(mask)
        assert result.mask.render() == source
        assert any("python_ast_summarize" in str(w.message) for w in caught)

    def test_null_byte_content_fails_open(self) -> None:
        """A null byte is rejected by ast.parse() before any syntax checking
        (as SyntaxError or ValueError, depending on Python version) — not
        caught here, same fail-open contract as a real syntax error."""
        mask = ContentMask.from_text("def f():\n    pass\n\x00")
        with pytest.raises((SyntaxError, ValueError), match="null byte"):
            self.stage.apply(mask, self._config())

    def test_decorators_preserved(self) -> None:
        source = (
            "class Foo:\n"
            "    @staticmethod\n"
            "    @cached\n"
            "    def bar(x):\n"
            '        """Bar docstring."""\n'
            "        y = x * 2\n"
            "        return y\n"
        )
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        for idx in range(5):  # class, both decorators, def, docstring
            assert result.lines[idx].decision is Decision.KEEP, f"line {idx}"
        assert result.lines[5].decision is Decision.COMPRESS  # y = x * 2
        assert result.lines[6].decision is Decision.COMPRESS  # return y

    def test_nested_classes_and_functions(self) -> None:
        source = (
            "class Outer:\n"  # 1
            "    class Inner:\n"  # 2
            "        def method(self):\n"  # 3
            '            """Inner method."""\n'  # 4
            "            do_something()\n"  # 5
            "            return 1\n"  # 6
            "\n"  # 7
            "    def outer_method(self):\n"  # 8
            "        def helper():\n"  # 9
            "            return 2\n"  # 10
            "        return helper()\n"  # 11
        )
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        expected = {
            0: Decision.KEEP,  # class Outer:
            1: Decision.KEEP,  # class Inner:
            2: Decision.KEEP,  # def method(self):
            3: Decision.KEEP,  # docstring
            4: Decision.COMPRESS,  # do_something()
            5: Decision.COMPRESS,  # return 1
            6: Decision.KEEP,  # blank line between methods
            7: Decision.KEEP,  # def outer_method(self):
            8: Decision.COMPRESS,  # def helper(): (nested — swallowed by outer_method)
            9: Decision.COMPRESS,  # return 2
            10: Decision.COMPRESS,  # return helper()
        }
        for idx, decision in expected.items():
            assert result.lines[idx].decision is decision, f"line {idx}"

    def test_async_functions(self) -> None:
        source = (
            "async def fetch(url):\n"
            '    """Fetch a URL."""\n'
            "    response = await client.get(url)\n"
            "    return response\n"
        )
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP  # async def fetch(url):
        assert result.lines[1].decision is Decision.KEEP  # docstring
        assert result.lines[2].decision is Decision.COMPRESS  # response = await ...
        assert result.lines[3].decision is Decision.COMPRESS  # return response

    def test_single_line_function_body_left_untouched(self) -> None:
        """Regression: a same-line body (`def f(): return 1`) shares its
        line with the signature. ContentMask can't compress half a line, so
        this must stay fully KEEP rather than deleting the signature."""
        source = "def f(): return 1\nx = f()\n"
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[0].line == "def f(): return 1"
        assert result.lines[1].decision is Decision.KEEP

    def test_docstring_only_body_left_untouched(self) -> None:
        source = 'def f():\n    """Just a docstring."""\n'
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.KEEP

    def test_large_file_compresses_every_function_body(self) -> None:
        n = 300
        chunks = [
            f"def func_{i}(x):\n    \"\"\"Docstring {i}.\"\"\"\n    y = x + {i}\n    return y\n"
            for i in range(n)
        ]
        source = "".join(chunks)
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        kept = sum(1 for lm in result.lines if lm.decision is Decision.KEEP)
        compressed = sum(1 for lm in result.lines if lm.decision is Decision.COMPRESS)
        # Each function: 2 kept lines (signature + docstring), 2 compressed (body),
        # plus one trailing blank line from the final chunk's terminating "\n".
        assert kept == n * 2 + 1
        assert compressed == n * 2

    def test_unicode_identifiers_and_docstrings_preserved(self) -> None:
        source = (
            "def café(x):\n"
            '    """Résumé: 日本語のコメント."""\n'
            "    y = x\n"
            "    return y\n"
        )
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[0].line == "def café(x):"
        assert result.lines[1].decision is Decision.KEEP
        assert result.lines[1].line == '    """Résumé: 日本語のコメント."""'
        assert result.lines[2].decision is Decision.COMPRESS
        assert result.lines[3].decision is Decision.COMPRESS

    def test_kept_lines_are_byte_identical_to_source(self) -> None:
        """No rewriting/reformatting ever happens: every non-COMPRESS line
        must match the original source line exactly, including a
        multi-line docstring's internal blank line."""
        source = (
            "import os\n"
            "\n"
            "CONST = 1\n"
            "\n"
            "\n"
            "def process(data, *, flag=False):\n"
            '    """Process data.\n'
            "\n"
            "    Multi-line docstring.\n"
            '    """\n'
            "    result = []\n"
            "    for item in data:\n"
            "        result.append(item)\n"
            "    return result\n"
        )
        original_lines = source.split("\n")
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        for idx, lm in enumerate(result.lines):
            if lm.decision is not Decision.COMPRESS:
                assert lm.line == original_lines[idx], f"line {idx} was modified"
        # The multi-line docstring (including its internal blank line) is
        # fully preserved; only the loop body afterward is compressed.
        for idx in range(6, 10):
            assert result.lines[idx].decision is Decision.KEEP
        for idx in range(10, 14):
            assert result.lines[idx].decision is Decision.COMPRESS

    def test_protect_line_never_compressed(self) -> None:
        lines = (
            LineMask(line="def foo():", decision=Decision.KEEP),
            _protect("    critical_body_line()"),
            LineMask(line="    return 1", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[1].line == "    critical_body_line()"
        assert result.lines[2].decision is Decision.COMPRESS

    def test_already_compressed_line_passthrough(self) -> None:
        """Line numbering must stay aligned to mask.lines even when an
        earlier stage already compressed a line (mask.render() would have
        dropped it and shifted every subsequent ast line number)."""
        lines = (
            LineMask(line="def foo():", decision=Decision.KEEP),
            _compress("    noise()"),
            LineMask(line="    return 1", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[1].line == "    noise()"
        assert result.lines[2].decision is Decision.COMPRESS  # return 1: real body line

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = "def foo():\n    CRITICAL_MARKER = True\n    return 1\n"
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["CRITICAL_MARKER"])
        result = self.stage.apply(mask, config)
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[2].decision is Decision.COMPRESS
