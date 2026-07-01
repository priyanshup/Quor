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

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages import _utils
from quor.pipeline.stages.deduplicate_consecutive import (
    DeduplicateConsecutiveConfig,
    DeduplicateConsecutiveStage,
)
from quor.pipeline.stages.group_repeated import GroupRepeatedConfig, GroupRepeatedStage
from quor.pipeline.stages.max_tokens import MaxTokensConfig, MaxTokensStage
from quor.pipeline.stages.remove_ansi import RemoveAnsiConfig, RemoveAnsiStage
from quor.pipeline.stages.strip_lines import StripLinesConfig, StripLinesStage

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
