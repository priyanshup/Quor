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

from quor.pipeline.ast_summarize import registry as ast_registry
from quor.pipeline.engine import Pipeline, StageEntry
from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages import _utils
from quor.pipeline.stages.code_ast_summarize import (
    CodeAstSummarizeConfig,
    CodeAstSummarizeStage,
)
from quor.pipeline.stages.collapse_unchanged_context import (
    CollapseUnchangedContextConfig,
    CollapseUnchangedContextStage,
)
from quor.pipeline.stages.column_padding_compression import (
    ColumnPaddingCompressionConfig,
    ColumnPaddingCompressionStage,
)
from quor.pipeline.stages.deduplicate_consecutive import (
    DeduplicateConsecutiveConfig,
    DeduplicateConsecutiveStage,
)
from quor.pipeline.stages.group_repeated import GroupRepeatedConfig, GroupRepeatedStage
from quor.pipeline.stages.match_output import MatchOutputConfig, MatchOutputStage
from quor.pipeline.stages.max_tokens import MaxTokensConfig, MaxTokensStage
from quor.pipeline.stages.numeric_range_compression import (
    NumericRangeCompressionConfig,
    NumericRangeCompressionStage,
)
from quor.pipeline.stages.path_prefix_fold import PathPrefixFoldConfig, PathPrefixFoldStage
from quor.pipeline.stages.protect_diff_filename_headers import (
    ProtectDiffFilenameHeadersConfig,
    ProtectDiffFilenameHeadersStage,
)
from quor.pipeline.stages.python_ast_summarize import (
    PythonAstSummarizeConfig,
    PythonAstSummarizeStage,
)
from quor.pipeline.stages.regex_replace import (
    RegexReplaceConfig,
    RegexReplaceRule,
    RegexReplaceStage,
)
from quor.pipeline.stages.relative_timestamp_compression import (
    RelativeTimestampCompressionConfig,
    RelativeTimestampCompressionStage,
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
        exact_match: bool = False,
    ) -> GroupRepeatedConfig:
        return GroupRepeatedConfig(
            type="group_repeated",
            patterns=patterns or [],
            min_count=min_count,
            preserve_patterns=preserve or [],
            exact_match=exact_match,
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

    # -- exact_match (QB-006B): opt-in strict mode, default-off ---------------

    def test_exact_match_default_false_preserves_shape_only_behavior(self) -> None:
        """Regression: not passing exact_match must behave exactly as before
        this field existed — this is what mypy's build.toml config relies on
        (same error message, different line numbers, still collapses)."""
        mask = ContentMask.from_text(
            "file.py:12: error: incompatible type\nfile.py:34: error: incompatible type"
        )
        result = self.stage.apply(mask, self._config(patterns=[r"^.*: error: "], min_count=2))
        assert "×2" in result.lines[0].line  # noqa: RUF001
        assert result.lines[1].decision is Decision.COMPRESS

    def test_exact_match_true_collapses_byte_identical_lines(self) -> None:
        mask = ContentMask.from_text("  1:1  error  Missing semicolon  semi\n" * 2)
        result = self.stage.apply(
            mask, self._config(patterns=[r"^\s*\d+:\d+\s+error\s"], min_count=2, exact_match=True)
        )
        assert "×2" in result.lines[0].line  # noqa: RUF001
        assert result.lines[1].decision is Decision.COMPRESS

    def test_exact_match_true_does_not_collapse_different_line_numbers(self) -> None:
        """Same rule/message, different line:col — same shape, different text.
        With exact_match, this must NOT collapse."""
        text = (
            "  1:1  error  Missing semicolon  semi\n"
            "  2:1  error  Missing semicolon  semi\n"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(
            mask, self._config(patterns=[r"^\s*\d+:\d+\s+error\s"], min_count=2, exact_match=True)
        )
        assert all(lm.decision is Decision.KEEP for lm in result.lines)
        assert "(×" not in result.lines[0].line  # noqa: RUF001
        assert "(×" not in result.lines[1].line  # noqa: RUF001

    def test_exact_match_true_does_not_collapse_different_rule_names(self) -> None:
        """Same shape, different rule/message entirely — must stay separate."""
        text = (
            "  1:1  error  Missing semicolon  semi\n"
            "  1:2  error  Unexpected console statement  no-console\n"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(
            mask, self._config(patterns=[r"^\s*\d+:\d+\s+error\s"], min_count=2, exact_match=True)
        )
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_exact_match_true_run_partially_collapses_around_a_different_line(self) -> None:
        """Two identical lines, then a different one, then two more identical
        (matching the first pair's text) must form two separate collapses,
        not one — the differing line in the middle must break the run."""
        text = (
            "  1:1  error  Missing semicolon  semi\n"
            "  1:1  error  Missing semicolon  semi\n"
            "  2:5  error  Unexpected console statement  no-console\n"
            "  1:1  error  Missing semicolon  semi\n"
            "  1:1  error  Missing semicolon  semi\n"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(
            mask, self._config(patterns=[r"^\s*\d+:\d+\s+error\s"], min_count=2, exact_match=True)
        )
        assert "×2" in result.lines[0].line  # noqa: RUF001
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.KEEP
        assert "no-console" in result.lines[2].line
        assert "×2" in result.lines[3].line  # noqa: RUF001
        assert result.lines[4].decision is Decision.COMPRESS

    # -- location_pattern (QB-044 slice 1): pytest-only, location-normalized --

    def _location_config(self, min_count: int = 2) -> GroupRepeatedConfig:
        return GroupRepeatedConfig(
            type="group_repeated",
            patterns=[r"^FAILED\s+\S+\s+-\s+"],
            location_pattern=r"^FAILED\s+(\S+)\s+-\s+",
            min_count=min_count,
        )

    def test_location_pattern_collapses_same_message_different_location(self) -> None:
        text = (
            "FAILED tests/test_math.py::test_add[1] - AssertionError: must be positive\n"
            "FAILED tests/test_math.py::test_add[2] - AssertionError: must be positive\n"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._location_config())
        # First occurrence kept byte-for-byte unmodified — no suffix appended.
        assert result.lines[0].line == "FAILED tests/test_math.py::test_add[1] - AssertionError: must be positive"
        assert result.lines[0].decision is Decision.KEEP
        # A new summary line referencing the repeated location is inserted.
        assert result.lines[1].decision is Decision.KEEP
        assert "1 more with the same message at:" in result.lines[1].line
        assert "test_add[2]" in result.lines[1].line
        # The original repeated line is compressed away.
        assert result.lines[2].decision is Decision.COMPRESS
        assert result.lines[2].line == "FAILED tests/test_math.py::test_add[2] - AssertionError: must be positive"

    def test_location_pattern_never_merges_different_messages(self) -> None:
        text = (
            "FAILED tests/test_a.py::test_x - AssertionError: message one\n"
            "FAILED tests/test_b.py::test_y - AssertionError: message two\n"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._location_config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)
        assert "more with the same message" not in result.render()

    def test_location_pattern_below_min_count_left_untouched(self) -> None:
        text = "FAILED tests/test_math.py::test_add[1] - AssertionError: must be positive"
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._location_config(min_count=2))
        assert len(result.lines) == 1
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[0].line == "FAILED tests/test_math.py::test_add[1] - AssertionError: must be positive"

    def test_location_pattern_protect_line_breaks_run(self) -> None:
        lines = (
            LineMask(line="FAILED tests/test_math.py::test_add[1] - AssertionError: must be positive"),
            _protect("FAILED tests/test_math.py::test_add[2] - AssertionError: must be positive"),
            LineMask(line="FAILED tests/test_math.py::test_add[3] - AssertionError: must be positive"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._location_config())
        # PROTECT line splits the run into two singleton groups — neither
        # meets min_count=2, so nothing collapses and PROTECT is untouched.
        assert result.lines[1].decision is Decision.PROTECT
        assert all(lm.decision is not Decision.COMPRESS for lm in result.lines)

    def test_location_pattern_does_not_affect_other_filters_default_none(self) -> None:
        """location_pattern defaults to None — existing shape/exact_match
        behavior for other filters (mypy, eslint, npm, ...) is untouched."""
        mask = ContentMask.from_text("WARNING: foo\nWARNING: foo")
        result = self.stage.apply(mask, self._config(patterns=["^WARNING:"], min_count=2))
        assert "×2" in result.lines[0].line  # noqa: RUF001


# ---------------------------------------------------------------------------
# group_repeated: scope="global" (QB-044 slice 2)
# ---------------------------------------------------------------------------


class TestGroupRepeatedGlobalScope:
    stage = GroupRepeatedStage()

    def _global_config(self, min_count: int = 2) -> GroupRepeatedConfig:
        return GroupRepeatedConfig(
            type="group_repeated",
            patterns=[r"^FAILED\s+\S+\s+-\s+"],
            location_pattern=r"^FAILED\s+(\S+)\s+-\s+",
            min_count=min_count,
            scope="global",
        )

    def test_separated_duplicates_collapse(self) -> None:
        """The core slice-2 case: two occurrences of the same message,
        separated by an unrelated failure, still collapse — the whole
        point of scope='global' over the adjacency-only default."""
        lines = (
            LineMask(line="FAILED tests/test_math.py::test_add[1] - AssertionError: must be positive"),
            LineMask(line="FAILED tests/test_other.py::test_x - AssertionError: unrelated failure"),
            LineMask(line="FAILED tests/test_math.py::test_add[2] - AssertionError: must be positive"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._global_config())

        assert result.lines[0].line == lines[0].line
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.KEEP
        assert "1 more with the same message at:" in result.lines[1].line
        assert "test_add[2]" in result.lines[1].line
        assert result.lines[2].line == lines[1].line
        assert result.lines[2].decision is Decision.KEEP
        assert result.lines[3].line == lines[2].line
        assert result.lines[3].decision is Decision.COMPRESS
        assert len(result.lines) == 4

    def test_different_messages_never_merge(self) -> None:
        lines = (
            LineMask(line="FAILED tests/test_a.py::test_x - AssertionError: message one"),
            LineMask(line="FAILED tests/test_b.py::test_y - AssertionError: message two"),
            LineMask(line="FAILED tests/test_c.py::test_z - AssertionError: message three"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._global_config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)
        assert "more with the same message" not in result.render()
        assert len(result.lines) == 3

    def test_distinct_normalized_keys_remain_separate_groups(self) -> None:
        """Two independently-repeating messages must each collapse into
        their *own* group — never cross-contaminate one summary with the
        other group's location."""
        lines = (
            LineMask(line="FAILED t::a1 - AssertionError: message A"),
            LineMask(line="FAILED t::b1 - AssertionError: message B"),
            LineMask(line="FAILED t::a2 - AssertionError: message A"),
            LineMask(line="FAILED t::b2 - AssertionError: message B"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._global_config())

        summaries = [lm.line for lm in result.lines if "more with the same message at:" in lm.line]
        assert len(summaries) == 2
        assert any("t::a2" in s for s in summaries)
        assert any("t::b2" in s for s in summaries)
        for s in summaries:
            assert not ("t::a2" in s and "t::b2" in s)

    def test_protect_line_never_touched_or_counted_as_group_member(self) -> None:
        """A PROTECT line sharing the same normalized key as a repeating
        group must never be modified, and must never be pulled into that
        group's count/summary — PROTECT is invisible to grouping entirely."""
        lines = (
            LineMask(line="FAILED t::a1 - AssertionError: same message"),
            _protect("FAILED t::a2 - AssertionError: same message"),
            LineMask(line="FAILED t::a3 - AssertionError: same message"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._global_config())

        # The PROTECT line keeps its own position and text exactly, wherever
        # the summary insertion (right after the first occurrence) lands it.
        protect_lines = [lm for lm in result.lines if lm.decision is Decision.PROTECT]
        assert len(protect_lines) == 1
        assert protect_lines[0].line == "FAILED t::a2 - AssertionError: same message"
        # The PROTECT line's location must never appear in a summary.
        summary = next(lm.line for lm in result.lines if "more with the same message at:" in lm.line)
        assert "t::a2" not in summary
        assert "t::a3" in summary

    def test_ordering_preserved_across_two_interleaved_groups(self) -> None:
        """Relative order of every surviving line must match the input's
        order exactly — grouping only ever removes non-first duplicates
        and inserts a summary right after each group's first occurrence."""
        lines = (
            LineMask(line="FAILED t::a1 - AssertionError: message A"),
            LineMask(line="FAILED t::b1 - AssertionError: message B"),
            LineMask(line="FAILED t::a2 - AssertionError: message A"),
            LineMask(line="FAILED t::b2 - AssertionError: message B"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._global_config())

        assert len(result.lines) == 6
        assert result.lines[0].line == lines[0].line and result.lines[0].decision is Decision.KEEP
        assert "t::a2" in result.lines[1].line and result.lines[1].decision is Decision.KEEP
        assert result.lines[2].line == lines[1].line and result.lines[2].decision is Decision.KEEP
        assert "t::b2" in result.lines[3].line and result.lines[3].decision is Decision.KEEP
        assert result.lines[4].line == lines[2].line and result.lines[4].decision is Decision.COMPRESS
        assert result.lines[5].line == lines[3].line and result.lines[5].decision is Decision.COMPRESS

    def test_default_scope_run_is_backward_compatible(self) -> None:
        """Without scope='global' (the default, unchanged), the exact same
        non-adjacent input from test_separated_duplicates_collapse must NOT
        collapse — proving the new mode is strictly opt-in and every
        existing filter's behavior is untouched."""
        lines = (
            LineMask(line="FAILED tests/test_math.py::test_add[1] - AssertionError: must be positive"),
            LineMask(line="FAILED tests/test_other.py::test_x - AssertionError: unrelated failure"),
            LineMask(line="FAILED tests/test_math.py::test_add[2] - AssertionError: must be positive"),
        )
        mask = ContentMask(lines=lines)
        config = GroupRepeatedConfig(
            type="group_repeated",
            patterns=[r"^FAILED\s+\S+\s+-\s+"],
            location_pattern=r"^FAILED\s+(\S+)\s+-\s+",
            min_count=2,
            # scope intentionally omitted — defaults to "run"
        )
        result = self.stage.apply(mask, config)

        assert len(result.lines) == 3
        assert all(lm.decision is Decision.KEEP for lm in result.lines)
        for original, actual in zip(lines, result.lines, strict=True):
            assert actual.line == original.line


# ---------------------------------------------------------------------------
# collapse_unchanged_context (QB-041)
# ---------------------------------------------------------------------------

class TestCollapseUnchangedContext:
    stage = CollapseUnchangedContextStage()

    def _config(self, context_lines: int = 3) -> CollapseUnchangedContextConfig:
        return CollapseUnchangedContextConfig(
            type="collapse_unchanged_context",
            context_lines=context_lines,
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_short_run_left_untouched(self) -> None:
        text = "\n".join(f"ctx {i}" for i in range(5))
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config(context_lines=3))
        assert all(lm.decision is Decision.KEEP for lm in result.lines)
        assert "omitted" not in result.render()

    def test_long_run_collapsed_with_window_preserved(self) -> None:
        lines = (
            *(LineMask(line=f"a fairly long unchanged context line number {i}") for i in range(10)),
            _protect("+edit"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(context_lines=3))
        rendered = result.render()
        assert "context line number 0" in rendered
        assert "context line number 1" in rendered
        assert "context line number 2" in rendered
        assert "context line number 7" in rendered
        assert "context line number 8" in rendered
        assert "context line number 9" in rendered
        assert "unchanged lines omitted" in rendered
        assert "context line number 4" not in rendered
        assert "+edit" in rendered

    def test_line_count_unchanged_after_collapse(self) -> None:
        """Base-class invariant: total LineMask entries stay the same size
        (collapse_unchanged_context reuses one line as the placeholder,
        like group_repeated does, rather than inserting a new one)."""
        text = "\n".join(f"a fairly long unchanged context line number {i}" for i in range(10))
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config(context_lines=3))
        assert "unchanged lines omitted" in result.render()
        assert len(result.lines) == len(mask.lines)

    def test_protect_lines_bound_run_and_are_never_modified(self) -> None:
        lines = (
            _protect("@@ -1,12 +1,12 @@"),
            *(LineMask(line=f"ctx {i}") for i in range(10)),
            _protect("-old"),
            _protect("+new"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(context_lines=3))
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].line == "@@ -1,12 +1,12 @@"
        assert result.lines[-2].line == "-old"
        assert result.lines[-1].line == "+new"
        assert result.lines[-2].decision is Decision.PROTECT
        assert result.lines[-1].decision is Decision.PROTECT

    def test_placeholder_not_smaller_not_collapsed(self) -> None:
        """Middle made of very short lines: placeholder cost (estimated
        tokens for "... N unchanged lines omitted ...") is >= the middle's
        own token cost, so the run must be left uncollapsed."""
        lines = (
            _protect("-old"),
            LineMask(line="a"),
            LineMask(line="b"),
            _protect("+new"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(context_lines=0))
        assert all(lm.decision is Decision.KEEP for lm in result.lines if lm.line in ("a", "b"))
        assert "omitted" not in result.render()

    def test_placeholder_strictly_smaller_collapses(self) -> None:
        """Middle made of token-dense lines whose combined cost strictly
        exceeds the placeholder's cost must collapse."""
        text = "\n".join(f"a fairly long unchanged context line number {i}" for i in range(6))
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config(context_lines=1))
        assert "unchanged lines omitted" in result.render()

    def test_context_lines_zero_collapses_whole_run(self) -> None:
        text = "\n".join(f"a fairly long unchanged context line number {i}" for i in range(5))
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config(context_lines=0))
        rendered = result.render()
        assert "5 unchanged lines omitted" in rendered
        assert "context line number 0" not in rendered

    def test_compress_lines_are_run_boundaries(self) -> None:
        lines = (
            *(LineMask(line=f"a fairly long unchanged context line number {i}") for i in range(10)),
            _compress("index abc..def"),
            *(LineMask(line=f"a fairly long unchanged context line number {i}") for i in range(10, 20)),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(context_lines=3))
        rendered = result.render()
        # Two separate runs, each collapsed independently
        assert rendered.count("unchanged lines omitted") == 2
        assert "index abc..def" not in rendered

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="CollapseUnchangedContextConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))


# ---------------------------------------------------------------------------
# protect_diff_filename_headers
# ---------------------------------------------------------------------------

class TestProtectDiffFilenameHeaders:
    stage = ProtectDiffFilenameHeadersStage()

    def _config(self) -> ProtectDiffFilenameHeadersConfig:
        return ProtectDiffFilenameHeadersConfig(type="protect_diff_filename_headers")

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_no_diff_header_left_untouched(self) -> None:
        mask = ContentMask.from_text("hello\nworld")
        result = self.stage.apply(mask, self._config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_mode_only_change_protects_header(self) -> None:
        text = "diff --git a/script.sh b/script.sh\nold mode 100644\nnew mode 100755"
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].line == "diff --git a/script.sh b/script.sh"

    def test_new_empty_file_protects_header(self) -> None:
        text = "diff --git a/empty.txt b/empty.txt\nnew file mode 100644\nindex 0000000..e69de29"
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.PROTECT

    def test_deleted_empty_file_protects_header(self) -> None:
        text = (
            "diff --git a/empty.txt b/empty.txt\ndeleted file mode 100644\n"
            "index e69de29..0000000"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.PROTECT

    def test_normal_content_diff_header_left_keep(self) -> None:
        """--- a/ and +++ b/ already carry the filename — the header must
        stay KEEP (still removable by strip_lines downstream), not PROTECT,
        or a normal diff would regress to carrying the filename twice."""
        text = (
            "diff --git a/foo.py b/foo.py\nindex abc..def 100644\n"
            "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,4 @@\n+new line"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP

    def test_binary_diff_header_left_keep(self) -> None:
        text = (
            "diff --git a/img.png b/img.png\nindex abc..def 100644\n"
            "Binary files a/img.png and b/img.png differ"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP

    def test_rename_diff_header_left_keep(self) -> None:
        text = (
            "diff --git a/a.py b/b.py\nsimilarity index 100%\n"
            "rename from a.py\nrename to b.py"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.KEEP

    def test_multi_file_diff_each_segment_judged_independently(self) -> None:
        """One mode-only file followed by one normal-content file: only the
        first file's header should be protected, not the second's."""
        text = (
            "diff --git a/mode.sh b/mode.sh\nold mode 100644\nnew mode 100755\n"
            "diff --git a/foo.py b/foo.py\nindex abc..def 100644\n"
            "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,4 @@\n+new line"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        headers = [lm for lm in result.lines if lm.line.startswith("diff --git ")]
        assert len(headers) == 2
        assert headers[0].decision is Decision.PROTECT
        assert headers[1].decision is Decision.KEEP

    def test_already_protected_line_from_earlier_stage_stays_protected(self) -> None:
        lines = (
            LineMask(
                line="diff --git a/x.sh b/x.sh",
                decision=Decision.PROTECT,
                reason="already decided",
                stage="test",
            ),
            LineMask(line="old mode 100644"),
            LineMask(line="new mode 100755"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].reason == "already decided"

    def test_line_count_unchanged(self) -> None:
        text = "diff --git a/script.sh b/script.sh\nold mode 100644\nnew mode 100755"
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert len(result.lines) == len(mask.lines)

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="ProtectDiffFilenameHeadersConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))


# ---------------------------------------------------------------------------
# path_prefix_fold
# ---------------------------------------------------------------------------

class TestPathPrefixFold:
    stage = PathPrefixFoldStage()

    def _config(
        self,
        patterns: list[str] | None = None,
        separator: str = "/",
        preserve: list[str] | None = None,
    ) -> PathPrefixFoldConfig:
        return PathPrefixFoldConfig(
            type="path_prefix_fold",
            patterns=patterns if patterns is not None else [r"/"],
            separator=separator,
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_no_patterns_configured_returns_mask_unchanged(self) -> None:
        mask = ContentMask.from_text("src/quor/foo.py\nsrc/quor/bar.py")
        result = self.stage.apply(mask, self._config(patterns=[]))
        assert result.render() == mask.render()

    def test_no_matching_lines_all_kept_unchanged(self) -> None:
        mask = ContentMask.from_text("line_a\nline_b\nline_c")
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_no_shared_separator_left_unfolded(self) -> None:
        """Matching lines share a real character prefix, but it contains no
        `/` at all (cut == -1) — there is no directory boundary to trim
        back to, so the run cannot be folded even though the strings
        overlap."""
        mask = ContentMask.from_text("abc/foo.py\nabc-bar.py")
        result = self.stage.apply(mask, self._config(patterns=[r"^abc"]))
        assert result.render() == mask.render()

    def test_cheap_run_left_unfolded(self) -> None:
        """Two short, similar-length lines: header overhead (prefix repeated
        once, plus punctuation) is not strictly cheaper than leaving them
        as-is, so the token-cost gate (QB-055's principle) declines to fold."""
        mask = ContentMask.from_text("src/quor/a.py\nsrc/quor/b.py")
        result = self.stage.apply(mask, self._config())
        assert "entries" not in result.render()
        assert result.render() == mask.render()

    def test_long_run_folded_with_header_and_suffixes(self) -> None:
        text = "\n".join(
            [
                "src/quor/pipeline/stages/foo.py",
                "src/quor/pipeline/stages/bar.py",
                "src/quor/pipeline/stages/baz.py",
            ]
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        rendered = result.render()
        assert "src/quor/pipeline/stages/ (3 entries):" in rendered
        assert "foo.py" in rendered
        assert "bar.py" in rendered
        assert "baz.py" in rendered
        assert "src/quor/pipeline/stages/foo.py" not in rendered

    def test_line_count_increases_by_one_header_per_fold(self) -> None:
        """The one documented exception besides group_repeated (see
        mask.py's module docstring): folding inserts exactly one new header
        LineMask ahead of the run, on top of the run's own (rewritten, not
        removed) lines."""
        text = "\n".join(
            [
                "src/quor/pipeline/stages/foo.py",
                "src/quor/pipeline/stages/bar.py",
                "src/quor/pipeline/stages/baz.py",
            ]
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert len(result.lines) == len(mask.lines) + 1

    def test_fold_is_reconstructible_byte_for_byte(self) -> None:
        """Losslessness claim: header-prefix + each child's suffix must
        equal that child's original line, for every folded entry."""
        originals = [
            "src/quor/pipeline/stages/foo.py",
            "src/quor/pipeline/stages/bar.py",
            "src/quor/pipeline/stages/baz.py",
        ]
        mask = ContentMask.from_text("\n".join(originals))
        result = self.stage.apply(mask, self._config())
        header = result.lines[0].line
        assert header.endswith(" (3 entries):")
        prefix = header[: -len(" (3 entries):")]
        children = [lm.line for lm in result.lines[1:]]
        assert [prefix + child for child in children] == originals

    def test_separator_boundary_never_splits_a_filename(self) -> None:
        """Filenames that happen to share a substring right after the real
        directory boundary ("co..." in both "collapse_..." and "code_...")
        must never be cut mid-name — the shared prefix is trimmed back to
        the last separator, not the raw longest-common-prefix."""
        text = "\n".join(
            [
                "src/quor/collapse_unchanged_context.py",
                "src/quor/code_ast_summarize.py",
                "src/quor/conftest.py",
                "src/quor/errors.py",
            ]
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        # The naive (untrimmed) LCP would have been "src/quor/co" — assert
        # the exact header and child lines, not just substring containment
        # (a substring check can't tell "collapse_...py" apart from a
        # wrongly-cut "llapse_...py" fragment, since the former contains the
        # latter as a substring).
        assert result.lines[0].line == "src/quor/ (4 entries):"
        children = [lm.line for lm in result.lines[1:]]
        assert children == [
            "collapse_unchanged_context.py",
            "code_ast_summarize.py",
            "conftest.py",
            "errors.py",
        ]

    def test_separator_is_configurable_dot(self) -> None:
        """QB-102: `separator` is a plain string, not hardcoded to '/' —
        dotted qualified names (Python modules, Java packages) front-code
        identically to filesystem paths, with zero stage-code changes."""
        text = "\n".join(
            [
                "quor.pipeline.stages.foo",
                "quor.pipeline.stages.bar",
                "quor.pipeline.stages.baz",
            ]
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(
            mask, self._config(patterns=[r"^\S+\.\S+$"], separator=".")
        )
        rendered = result.render()
        assert "quor.pipeline.stages. (3 entries):" in rendered
        assert "foo" in rendered
        assert "bar" in rendered
        assert "baz" in rendered
        assert "quor.pipeline.stages.foo" not in rendered

    def test_separator_is_configurable_multi_char(self) -> None:
        """QB-102: multi-character separators (C++/Rust '::', Gradle ':')
        work identically — the cut point is `str.rfind(separator)`, never
        assumed to be a single character."""
        text = "\n".join(
            [
                "quor::pipeline::mask",
                "quor::pipeline::stages",
                "quor::pipeline::registry",
            ]
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(
            mask, self._config(patterns=[r"^\S+::\S+$"], separator="::")
        )
        rendered = result.render()
        assert "quor::pipeline:: (3 entries):" in rendered
        assert "mask" in rendered
        assert "stages" in rendered
        assert "registry" in rendered

    def test_separator_reconstruction_is_lossless_for_non_slash_separator(self) -> None:
        """Same byte-for-byte reconstruction guarantee as
        test_fold_is_reconstructible_byte_for_byte, proven again for a
        non-'/' separator so the invariant is never accidentally
        slash-specific."""
        originals = [
            "java.util.concurrent.Future",
            "java.util.concurrent.Executor",
            "java.util.concurrent.CompletableFuture",
        ]
        mask = ContentMask.from_text("\n".join(originals))
        result = self.stage.apply(
            mask, self._config(patterns=[r"^\S+\.\S+$"], separator=".")
        )
        header = result.lines[0].line
        assert header.endswith(" (3 entries):")
        prefix = header[: -len(" (3 entries):")]
        children = [lm.line for lm in result.lines[1:]]
        assert [prefix + child for child in children] == originals

    def test_protect_lines_bound_run_and_are_never_modified(self) -> None:
        lines = (
            LineMask(line="src/quor/pipeline/stages/foo.py"),
            _protect("PROTECTED src/quor/pipeline/stages/bar.py"),
            LineMask(line="src/quor/pipeline/stages/baz.py"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        # Each side of the PROTECT line is a run of length 1 — too short to
        # fold (see test_cheap_run_left_unfolded's reasoning taken further:
        # _fold_run requires at least 2 lines outright).
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[1].line == "PROTECTED src/quor/pipeline/stages/bar.py"

    def test_compress_lines_are_run_boundaries(self) -> None:
        lines = (
            LineMask(line="src/quor/pipeline/stages/foo.py"),
            LineMask(line="src/quor/pipeline/stages/bar.py"),
            LineMask(line="src/quor/pipeline/stages/baz.py"),
            _compress("src/quor/pipeline/stages/qux.py"),
            LineMask(line="src/quor/pipeline/other/aaa.py"),
            LineMask(line="src/quor/pipeline/other/bbb.py"),
            LineMask(line="src/quor/pipeline/other/ccc.py"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        rendered = result.render()
        assert rendered.count("entries):") == 2
        assert "src/quor/pipeline/stages/ (3 entries):" in rendered
        assert "src/quor/pipeline/other/ (3 entries):" in rendered

    def test_preserve_patterns_excludes_line_and_can_prevent_folding(self) -> None:
        lines = (
            LineMask(line="src/quor/pipeline/stages/foo.py"),
            LineMask(line="src/quor/pipeline/stages/bar.py"),
            LineMask(line="src/quor/pipeline/stages/baz.py"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(
            mask, self._config(preserve=[r"bar\.py"])
        )
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[1].line == "src/quor/pipeline/stages/bar.py"
        # foo.py and baz.py are now two separate length-1 runs, split by the
        # PROTECT line — neither can fold.
        assert "entries):" not in result.render()

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="PathPrefixFoldConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))


class TestNumericRangeCompression:
    stage = NumericRangeCompressionStage()

    def _config(self, preserve: list[str] | None = None) -> NumericRangeCompressionConfig:
        return NumericRangeCompressionConfig(
            type="numeric_range_compression",
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_single_number_unchanged(self) -> None:
        mask = ContentMask.from_text("42")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "42"
        assert result.lines[0].decision is Decision.KEEP

    def test_no_numeric_lines_all_kept_unchanged(self) -> None:
        mask = ContentMask.from_text("line_a\nline_b\nline_c")
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_long_run_folded_to_range(self) -> None:
        mask = ContentMask.from_text("101\n102\n103\n104\n105")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "101-105"

    def test_multiple_runs_separated_by_gap_fold_independently(self) -> None:
        """Example 2 from QB-097's spec: two separate 3-item runs of
        2-digit numbers, broken by the 14 -> 18 gap."""
        mask = ContentMask.from_text("12\n13\n14\n18\n19\n20")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "12-14\n18-20"

    def test_interrupted_run_isolates_the_non_consecutive_tail(self) -> None:
        """Example 4 from QB-097's spec: mixed text ahead of a foldable run
        (the header line itself never matches the all-digits pattern, so it
        is just an ordinary run boundary, not special-cased)."""
        mask = ContentMask.from_text("Errors on lines:\n201\n202\n203")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "Errors on lines:\n201-203"

    def test_large_range_folds(self) -> None:
        numbers = [str(n) for n in range(1001, 1051)]
        mask = ContentMask.from_text("\n".join(numbers))
        result = self.stage.apply(mask, self._config())
        assert result.render() == "1001-1050"

    def test_two_digit_pair_ties_and_is_left_unfolded(self) -> None:
        """Documented design decision (see the stage's module docstring):
        joining two equal-width lines with '-' is always exactly as many
        characters as joining them with '\\n', so a same-width 2-line run
        can never be *strictly* cheaper — only tie. The gate requires
        strictly cheaper, so this never folds."""
        mask = ContentMask.from_text("42\n43")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "42\n43"

    def test_single_digit_pair_folds_when_strictly_cheaper(self) -> None:
        """The two-line case *can* fold: single-digit numbers are floored to
        1 token each (2 total), while '4-5' is short enough to stay at 1."""
        mask = ContentMask.from_text("4\n5")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "4-5"

    def test_never_merges_across_a_gap(self) -> None:
        """Example 5 from QB-097's spec ('do NOT merge'): 12 is isolated by
        the gap to 14 and must never be folded into a 12-15-style range."""
        mask = ContentMask.from_text("12\n14\n15")
        result = self.stage.apply(mask, self._config())
        assert "12-15" not in result.render()
        assert "12" in result.render().splitlines()

    def test_descending_numbers_never_merge(self) -> None:
        mask = ContentMask.from_text("43\n42\n41")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "43\n42\n41"

    def test_duplicate_numbers_never_merge(self) -> None:
        mask = ContentMask.from_text("12\n12\n13")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "12\n12\n13"

    def test_negative_numbers_never_merge(self) -> None:
        """Explicit design decision: '-' is the range separator, so merging
        negatives would be ambiguous ('-5--1'); negative lines are simply
        never run candidates, always left isolated."""
        mask = ContentMask.from_text("-3\n-2\n-1")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "-3\n-2\n-1"

    def test_leading_zeros_preserved_and_reconstructible(self) -> None:
        mask = ContentMask.from_text("001\n002\n003")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "001-003"

    def test_leading_zero_width_mismatch_never_merges(self) -> None:
        """'01' (width 2) can't safely join a width-3 padded run without
        reformatting a line to a width it wasn't written in — left alone."""
        mask = ContentMask.from_text("01\n002\n003")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "01\n002\n003"

    def test_width_crossing_natural_numbers_never_merge(self) -> None:
        """Documented conservative trade-off: a uniform-width run is
        required even though '9-11' would be an unambiguous, safe range —
        traded for one simple invariant instead of two code paths."""
        mask = ContentMask.from_text("9\n10\n11")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "9\n10\n11"

    def test_line_count_unchanged_by_folding(self) -> None:
        """Unlike path_prefix_fold, this stage never inserts a new LineMask
        — the run's first line is rewritten in place and the rest COMPRESS,
        so total line count is preserved (mask.py's group_repeated/
        collapse_unchanged_context exception, not a new one)."""
        mask = ContentMask.from_text("101\n102\n103\n104\n105")
        result = self.stage.apply(mask, self._config())
        assert len(result.lines) == len(mask.lines)

    def test_folded_lines_are_compressed_not_deleted(self) -> None:
        mask = ContentMask.from_text("101\n102\n103\n104\n105")
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].line == "101-105"
        assert result.lines[0].decision is Decision.KEEP
        assert [lm.decision for lm in result.lines[1:]] == [Decision.COMPRESS] * 4
        assert [lm.line for lm in result.lines[1:]] == ["102", "103", "104", "105"]

    def test_protect_lines_bound_run_and_are_never_modified(self) -> None:
        lines = (
            LineMask(line="101"),
            _protect("102"),
            LineMask(line="103"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[1].line == "102"
        assert "101-103" not in result.render()

    def test_compress_lines_are_run_boundaries(self) -> None:
        lines = (
            LineMask(line="101"),
            LineMask(line="102"),
            LineMask(line="103"),
            _compress("104"),
            LineMask(line="201"),
            LineMask(line="202"),
            LineMask(line="203"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        rendered = result.render()
        assert "101-103" in rendered
        assert "201-203" in rendered

    def test_preserve_patterns_excludes_line_and_can_prevent_folding(self) -> None:
        lines = (
            LineMask(line="101"),
            LineMask(line="102"),
            LineMask(line="103"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(preserve=[r"^102$"]))
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[1].line == "102"
        # 101 and 103 are now two separate length-1 runs, split by the
        # PROTECT line — neither can fold.
        assert "-" not in result.render()

    def test_mixed_text_lines_are_never_folded(self) -> None:
        """Documented scope decision: only standalone numeric lines are
        candidates. 'Line 101'-style lines with a constant text prefix are
        out of scope for this stage (see the module docstring)."""
        mask = ContentMask.from_text("Line 101\nLine 102\nLine 103")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "Line 101\nLine 102\nLine 103"

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="NumericRangeCompressionConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))


# ---------------------------------------------------------------------------
# relative_timestamp_compression (QB-098)
# ---------------------------------------------------------------------------

from quor.pipeline.stages import relative_timestamp_compression as _rtc  # noqa: E402


class TestRelativeTimestampCompression:
    stage = RelativeTimestampCompressionStage()

    def _config(self, preserve: list[str] | None = None) -> RelativeTimestampCompressionConfig:
        return RelativeTimestampCompressionConfig(
            type="relative_timestamp_compression",
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_single_timestamped_line_unchanged(self) -> None:
        """A single-line log has nothing to compute a delta against."""
        mask = ContentMask.from_text("2026-07-31 10:15:01 solo line")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "2026-07-31 10:15:01 solo line"
        assert result.lines[0].decision is Decision.KEEP

    def test_no_timestamp_lines_all_kept_unchanged(self) -> None:
        mask = ContentMask.from_text("line_a\nline_b\nline_c")
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_space_datetime_run_folds_to_per_line_deltas(self) -> None:
        mask = ContentMask.from_text(
            "2026-07-31 10:15:01 INFO Starting server\n"
            "2026-07-31 10:15:02 INFO Loading plugins\n"
            "2026-07-31 10:15:03 INFO Ready\n"
            "2026-07-31 10:15:04 INFO Listening on :8080"
        )
        result = self.stage.apply(mask, self._config())
        assert result.render() == (
            "2026-07-31 10:15:01 INFO Starting server\n"
            "+1s INFO Loading plugins\n"
            "+1s INFO Ready\n"
            "+1s INFO Listening on :8080"
        )

    def test_iso_z_with_milliseconds_exact_delta(self) -> None:
        """QB-098's own worked example: a 250ms then a 750ms gap, exact —
        never rounded, never approximated."""
        mask = ContentMask.from_text(
            "2026-07-31T10:15:01.000Z msg1\n"
            "2026-07-31T10:15:01.250Z msg2\n"
            "2026-07-31T10:15:02.000Z msg3"
        )
        result = self.stage.apply(mask, self._config())
        assert result.render() == (
            "2026-07-31T10:15:01.000Z msg1\n+250ms msg2\n+750ms msg3"
        )

    def test_largest_exact_unit_chosen_for_hours_then_minutes(self) -> None:
        mask = ContentMask.from_text(
            "2026-07-31 10:00:00 a\n2026-07-31 12:00:00 b\n2026-07-31 12:05:00 c"
        )
        result = self.stage.apply(mask, self._config())
        assert result.render() == "2026-07-31 10:00:00 a\n+2h b\n+5m c"

    def test_offset_normalized_to_utc_across_a_dst_style_shift(self) -> None:
        """Same local clock reading, different UTC offset — the underlying
        instant is what's compared, so this is a genuine +1h, not +0s."""
        mask = ContentMask.from_text(
            "2026-07-31T10:00:00+05:30 event-a\n2026-07-31T10:00:00+04:30 event-b"
        )
        result = self.stage.apply(mask, self._config())
        assert result.render() == "2026-07-31T10:00:00+05:30 event-a\n+1h event-b"

    def test_time_only_run_folds(self) -> None:
        mask = ContentMask.from_text("10:15:01 a\n10:15:02 b\n10:15:03 c")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "10:15:01 a\n+1s b\n+1s c"

    def test_malformed_hour_never_matches_and_never_folds(self) -> None:
        """QB-098 requirement: 'every timestamp parses successfully' — an
        out-of-range hour is not a timestamp at all, so it's an ordinary
        run-breaking line, and the whole input is left untouched."""
        mask = ContentMask.from_text("10:15:01 ok-a\n25:99:99 broken\n10:15:03 ok-b")
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()

    def test_invalid_calendar_date_never_matches(self) -> None:
        mask = ContentMask.from_text("2026-02-30 10:15:01 bogus\n2026-02-30 10:15:02 also bogus")
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()

    def test_mixed_formats_break_the_run(self) -> None:
        """A run only continues while every line matches the *same* format
        kind as the run's first line — mixing space_datetime and time_only
        (even though both are individually valid) never folds together."""
        mask = ContentMask.from_text(
            "2026-07-31 10:15:01 a\n10:15:02 b\n2026-07-31 10:15:03 c"
        )
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()
        assert "+" not in result.render()

    def test_fractional_width_change_breaks_the_run(self) -> None:
        """Same conservative 'identical width' invariant numeric_range_
        compression uses for digit width, applied to fractional-second
        digit count."""
        mask = ContentMask.from_text(
            "2026-07-31T10:15:01.1Z a\n2026-07-31T10:15:01.12Z b"
        )
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()

    def test_decreasing_timestamp_breaks_the_run(self) -> None:
        """A decrease could be clock skew or a log-rotation splice — 'when
        uncertain, don't collapse' wins; the line before the decrease is
        left as an isolated 1-line run, but a later ascending pair can still
        fold on its own."""
        mask = ContentMask.from_text(
            "2026-07-31 10:15:05 a\n2026-07-31 10:15:03 b\n2026-07-31 10:15:04 c"
        )
        result = self.stage.apply(mask, self._config())
        assert "2026-07-31 10:15:05 a" in result.render()
        assert "2026-07-31 10:15:03 b" in result.render()
        assert "+1s c" in result.render()

    def test_duplicate_timestamps_fold_to_a_zero_delta(self) -> None:
        mask = ContentMask.from_text("10:15:01 a\n10:15:01 b")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "10:15:01 a\n+0s b"

    def test_already_relative_lines_are_never_reinterpreted(self) -> None:
        mask = ContentMask.from_text("+1s already relative\n+2m also relative")
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()

    def test_bracket_wrapped_timestamps_are_out_of_scope(self) -> None:
        """Documented scope limit: the format must start at column 0."""
        mask = ContentMask.from_text(
            "[2026-07-31T10:15:01Z] a\n[2026-07-31T10:15:02Z] b"
        )
        result = self.stage.apply(mask, self._config())
        assert result.render() == mask.render()

    def test_epoch_integers_are_not_matched_by_this_stage(self) -> None:
        """Bare digit runs stay numeric_range_compression's territory."""
        mask = ContentMask.from_text("1785499701\n1785499702")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "1785499701\n1785499702"

    def test_line_count_unchanged_by_folding(self) -> None:
        mask = ContentMask.from_text("10:15:01 a\n10:15:02 b\n10:15:03 c")
        result = self.stage.apply(mask, self._config())
        assert len(result.lines) == len(mask.lines)

    def test_no_line_is_ever_compressed(self) -> None:
        """Unlike every sibling folding stage, this one never hides a line
        behind COMPRESS — every line in a folded run stays KEEP."""
        mask = ContentMask.from_text("10:15:01 a\n10:15:02 b\n10:15:03 c")
        result = self.stage.apply(mask, self._config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_first_line_of_a_fold_is_left_completely_untouched(self) -> None:
        mask = ContentMask.from_text("10:15:01 a\n10:15:02 b")
        result = self.stage.apply(mask, self._config())
        assert result.lines[0] is mask.lines[0]

    def test_protect_lines_bound_run_and_are_never_modified(self) -> None:
        lines = (
            LineMask(line="10:15:01 a"),
            _protect("10:15:02 b"),
            LineMask(line="10:15:03 c"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[1].line == "10:15:02 b"
        assert "+" not in result.render()

    def test_compress_lines_are_run_boundaries(self) -> None:
        lines = (
            LineMask(line="10:15:01 a"),
            LineMask(line="10:15:02 b"),
            _compress("10:15:03 c"),
            LineMask(line="10:20:00 d"),
            LineMask(line="10:20:01 e"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        rendered = result.render()
        assert "+1s b" in rendered
        assert "+1s e" in rendered

    def test_preserve_patterns_excludes_line_and_can_prevent_folding(self) -> None:
        lines = (
            LineMask(line="10:15:01 a"),
            LineMask(line="10:15:02 b"),
            LineMask(line="10:15:03 c"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(preserve=[r"^10:15:02"]))
        assert result.lines[1].decision is Decision.PROTECT
        # 10:15:01/10:15:03 are now two separate length-1 runs, split by the
        # PROTECT line — neither can fold.
        assert "+" not in result.render()

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="RelativeTimestampCompressionConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))

    # -- internal helpers, exercised directly (same convention as _utils) --

    def test_format_delta_ns_picks_the_largest_exact_unit(self) -> None:
        assert _rtc._format_delta_ns(0) == "+0s"
        assert _rtc._format_delta_ns(1) == "+1ns"
        assert _rtc._format_delta_ns(1_000) == "+1us"
        assert _rtc._format_delta_ns(1_000_000) == "+1ms"
        assert _rtc._format_delta_ns(1_000_000_000) == "+1s"
        assert _rtc._format_delta_ns(60_000_000_000) == "+1m"
        assert _rtc._format_delta_ns(3_600_000_000_000) == "+1h"
        # Doesn't divide evenly by any coarser unit -> falls through to ns.
        assert _rtc._format_delta_ns(1_000_001) == "+1000001ns"

    def test_parse_line_rejects_out_of_range_components(self) -> None:
        assert _rtc._parse_line("24:00:00 x") is None
        assert _rtc._parse_line("10:60:00 x") is None
        assert _rtc._parse_line("10:00:60 x") is None
        assert _rtc._parse_line("2026-13-01 10:00:00 x") is None
        assert _rtc._parse_line("2026-02-30 10:00:00 x") is None
        assert _rtc._parse_line("2026-07-31T10:00:00+24:00 x") is None

    def test_cost_gate_blocks_a_fold_that_would_cost_more(self) -> None:
        """Direct test of `_fold_run`'s token-cost gate: an adversarially
        long, non-round delta against very short original lines must leave
        the run untouched. This case can't be reached through a real
        timestamp match (see backlog.md's QB-098 entry: the gate is
        unreachable via any of the 7 supported formats in practice, since a
        full timestamp match is always at least 8 characters and every
        realistic delta encodes shorter than that) — constructed directly
        against `_fold_run` to still exercise the gate's own logic.
        """
        run = [
            (LineMask(line="aaaaaaaa"), 8, 0),
            (LineMask(line="bbbbbbbb"), 8, 123_456_789_012_345),
        ]
        result = _rtc._fold_run(run, "relative_timestamp_compression")
        assert [lm.line for lm in result] == ["aaaaaaaa", "bbbbbbbb"]

    def test_cost_gate_allows_a_fold_that_is_strictly_cheaper(self) -> None:
        run = [
            (LineMask(line="2026-07-31 10:15:01 a"), 19, 0),
            (LineMask(line="2026-07-31 10:15:02 b"), 19, 1_000_000_000),
        ]
        result = _rtc._fold_run(run, "relative_timestamp_compression")
        assert [lm.line for lm in result] == ["2026-07-31 10:15:01 a", "+1s b"]


# ---------------------------------------------------------------------------
# column_padding_compression (QB-101)
# ---------------------------------------------------------------------------

class TestColumnPaddingCompression:
    stage = ColumnPaddingCompressionStage()

    def _config(
        self,
        patterns: list[str] | None = None,
        max_gaps: int | None = None,
        preserve: list[str] | None = None,
    ) -> ColumnPaddingCompressionConfig:
        return ColumnPaddingCompressionConfig(
            type="column_padding_compression",
            patterns=patterns if patterns is not None else [r"\s{2,}"],
            max_gaps=max_gaps,
            preserve_patterns=preserve or [],
        )

    # -- boilerplate parity with sibling stages --------------------------

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_no_patterns_configured_returns_mask_unchanged(self) -> None:
        mask = ContentMask.from_text("a  b\nc  d")
        result = self.stage.apply(mask, self._config(patterns=[]))
        assert result.render() == mask.render()

    def test_no_matching_lines_all_kept_unchanged(self) -> None:
        mask = ContentMask.from_text("line_a\nline_b\nline_c")
        result = self.stage.apply(mask, self._config(patterns=[r"NEVER_MATCHES"]))
        assert result.render() == mask.render()
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="ColumnPaddingCompressionConfig"):
            self.stage.apply(ContentMask.from_text("x"), RemoveAnsiConfig(type="remove_ansi"))

    def test_line_count_never_changes(self) -> None:
        """No mask.py exception is needed for this stage (see its own module
        docstring) — it never inserts a line and never assigns COMPRESS."""
        mask = ContentMask.from_text(
            "NAME      READY\nfrontend  1/1\nbackend   1/1"
        )
        result = self.stage.apply(mask, self._config())
        assert len(result.lines) == len(mask.lines)
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_never_assigns_compress(self) -> None:
        mask = ContentMask.from_text("NAME      READY\nfrontend  1/1")
        result = self.stage.apply(mask, self._config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    # -- the worked example from the ticket ------------------------------

    def test_kubectl_style_table_collapses_to_single_spaces(self) -> None:
        text = (
            "NAME          READY   STATUS             RESTARTS   AGE\n"
            "frontend      1/1     Running            0          2d\n"
            "backend       1/1     Running            1          2d\n"
            "database      1/1     Pending            0          5m"
        )
        mask = ContentMask.from_text(text)
        result = self.stage.apply(mask, self._config())
        assert result.render() == (
            "NAME READY STATUS RESTARTS AGE\n"
            "frontend 1/1 Running 0 2d\n"
            "backend 1/1 Running 1 2d\n"
            "database 1/1 Pending 0 5m"
        )

    # -- required invariants: what must NEVER be touched -----------------

    def test_leading_indentation_preserved(self) -> None:
        mask = ContentMask.from_text("    NAME      READY")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "    NAME READY"

    def test_trailing_whitespace_preserved(self) -> None:
        mask = ContentMask.from_text("NAME      READY   ")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "NAME READY   "

    def test_tabs_never_touched_even_when_adjacent_to_a_space_run(self) -> None:
        """A tab is not `\\S`, so a run touching a tab on either side never
        matches — mixed tab+space padding is left completely alone, per
        this stage's own 'tabs untouched' requirement."""
        mask = ContentMask.from_text("a  \tb")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "a  \tb"

    def test_pure_tab_separated_content_untouched(self) -> None:
        mask = ContentMask.from_text("origin\thttps://example.com/repo.git (fetch)")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "origin\thttps://example.com/repo.git (fetch)"

    def test_markdown_table_untouched_when_filter_pattern_does_not_match_it(self) -> None:
        """Safety against markdown tables/ASCII art/box-drawing is a matter
        of filter-author opt-in scoping (see this stage's own module
        docstring), not stage-internal detection — this proves the
        practical guarantee: a real filter's declared row shape (here, a
        kubectl-style pattern requiring a slash-delimited READY-style
        token) never matches markdown table syntax, so it passes through
        untouched even though it does contain 2+-space-adjacent content in
        principle."""
        mask = ContentMask.from_text("| Name   | Age |\n|--------|-----|\n| Alice  | 30  |")
        result = self.stage.apply(mask, self._config(patterns=[r"^\S+\s+\d+/\d+\s"]))
        assert result.render() == mask.render()

    def test_ascii_art_untouched_when_filter_pattern_does_not_match_it(self) -> None:
        mask = ContentMask.from_text("  /\\_/\\  \n ( o.o ) \n  > ^ <  ")
        result = self.stage.apply(mask, self._config(patterns=[r"^\S+\s+\d+/\d+\s"]))
        assert result.render() == mask.render()

    def test_box_drawing_untouched_when_filter_pattern_does_not_match_it(self) -> None:
        mask = ContentMask.from_text("├── src\n│   └── main.py\n└── tests")
        result = self.stage.apply(mask, self._config(patterns=[r"^\S+\s+\d+/\d+\s"]))
        assert result.render() == mask.render()

    def test_code_sample_untouched_when_filter_pattern_does_not_match_it(self) -> None:
        mask = ContentMask.from_text(
            "def foo(a,   b):\n    return a  +  b  # aligned comment"
        )
        result = self.stage.apply(mask, self._config(patterns=[r"^\S+\s+\d+/\d+\s"]))
        assert result.render() == mask.render()

    def test_already_single_spaced_unchanged(self) -> None:
        mask = ContentMask.from_text("NAME READY STATUS")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "NAME READY STATUS"
        assert result.lines[0] is mask.lines[0]

    def test_mixed_spacing_only_multi_space_runs_collapse(self) -> None:
        mask = ContentMask.from_text("frontend 1/1     Running 0  2d")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "frontend 1/1 Running 0 2d"

    # -- cost gate (QB-055, same principle as every sibling stage) -------

    def test_equal_cost_two_space_run_stays_unchanged(self) -> None:
        """`"a  b"` (4 chars, ceil(4/4)=1 token) -> `"a b"` (3 chars,
        ceil(3/4)=1 token) — same estimated token count, so the strict
        "only if cheaper" gate declines, matching numeric_range_
        compression's identical two-digit-pair precedent."""
        mask = ContentMask.from_text("a  b")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "a  b"
        assert result.lines[0].decision is Decision.KEEP

    def test_token_gate_declines_when_collapse_does_not_cross_a_token_boundary(self) -> None:
        mask = ContentMask.from_text("ab  cd")
        result = self.stage.apply(mask, self._config())
        # "ab  cd" (6 chars, ceil(6/4)=2) -> "ab cd" (5 chars, ceil(5/4)=2):
        # still 2 estimated tokens either way, so this also ties and declines.
        assert result.render() == "ab  cd"

    def test_token_gate_allows_a_collapse_that_crosses_a_token_boundary(self) -> None:
        mask = ContentMask.from_text("frontend      1/1")
        result = self.stage.apply(mask, self._config())
        assert result.render() == "frontend 1/1"

    # -- filter opt-in (no heuristics) ------------------------------------

    def test_filter_opt_in_only_declared_pattern_lines_are_candidates(self) -> None:
        mask = ContentMask.from_text("NAME      READY\nprose  with  double  spaces  too")
        result = self.stage.apply(mask, self._config(patterns=[r"^NAME"]))
        rendered = result.render()
        assert "NAME READY" in rendered
        assert "prose  with  double  spaces  too" in rendered

    # -- PROTECT / COMPRESS boundaries ------------------------------------

    def test_protect_lines_never_modified(self) -> None:
        lines = (
            LineMask(line="NAME      READY"),
            _protect("PROTECTED    line"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[1].line == "PROTECTED    line"

    def test_compress_lines_never_modified(self) -> None:
        lines = (
            LineMask(line="NAME      READY"),
            _compress("compressed    line"),
        )
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[1].line == "compressed    line"

    def test_preserve_patterns_excludes_a_line_even_if_it_would_otherwise_match(self) -> None:
        lines = (LineMask(line="NAME      READY"),)
        mask = ContentMask(lines=lines)
        result = self.stage.apply(mask, self._config(preserve=[r"NAME"]))
        assert result.lines[0].decision is Decision.PROTECT
        assert result.lines[0].line == "NAME      READY"

    # -- max_gaps: the bug found during implementation --------------------

    def test_unbounded_collapse_would_corrupt_a_multi_space_filename(self) -> None:
        """Without max_gaps, a filename with its own double spaces gets
        corrupted — this is the real bug max_gaps exists to fix, pinned
        here as a regression test against reintroducing unbounded collapse
        on a free-text trailing column by default. Every metadata gap is
        generously (2-space) padded so this is unambiguous: 8 real
        metadata gaps precede the filename's own 3 internal gaps."""
        mask = ContentMask.from_text(
            "-rw-r--r--  1  dev  staff  42  Jul  1  22:41  my  file  with  spaces.txt"
        )
        result = self.stage.apply(mask, self._config())  # max_gaps=None (unbounded)
        assert "my file with spaces.txt" in result.render()
        assert "my  file  with  spaces.txt" not in result.render()

    def test_max_gaps_protects_a_trailing_free_text_column(self) -> None:
        """Same fixture as the unbounded test above, but with max_gaps=8 —
        exactly the number of real metadata gaps — so the filename's own
        3 internal gaps are provably never reached by the substitution at
        all (re.sub's count is exhausted exactly at the boundary), not
        merely "probably" left alone."""
        mask = ContentMask.from_text(
            "-rw-r--r--  1  dev  staff  42  Jul  1  22:41  my  file  with  spaces.txt"
        )
        result = self.stage.apply(mask, self._config(max_gaps=8))
        rendered = result.render()
        assert rendered.startswith("-rw-r--r-- 1 dev staff 42 Jul 1 22:41 ")
        assert "my  file  with  spaces.txt" in rendered

    def test_max_gaps_only_bounds_the_count_never_forces_extra_collapses(self) -> None:
        """A line with fewer real gaps than max_gaps collapses all of them
        (max_gaps is an upper bound via re.sub's own `count` semantics, not
        an exact requirement). Long enough tokens that the collapse
        provably crosses the ceil(len/4) token-estimate boundary, so this
        exercises max_gaps specifically, not the cost gate."""
        mask = ContentMask.from_text("alpha  bravo  charlie")
        result = self.stage.apply(mask, self._config(max_gaps=10))
        assert result.render() == "alpha bravo charlie"

    def test_max_gaps_one_collapses_only_the_first_gap(self) -> None:
        mask = ContentMask.from_text("alpha  bravo  charlie")
        result = self.stage.apply(mask, self._config(max_gaps=1))
        assert result.render() == "alpha bravo  charlie"

    # -- interaction with sibling fold stages (QB-095/QB-097/QB-098) -----

    def test_interaction_with_path_prefix_fold_header_line_is_left_alone(self) -> None:
        """A path_prefix_fold header line ("prefix (N entries):") has no
        multi-space run of its own — running column_padding_compression
        afterward on the same mask is a clean no-op for that line."""
        header = LineMask(
            line="src/quor/pipeline/stages/ (3 entries):",
            decision=Decision.KEEP,
            reason="folded 3 lines",
            stage="path_prefix_fold",
        )
        table_row = LineMask(line="frontend      1/1")
        mask = ContentMask((header, table_row))
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].line == "src/quor/pipeline/stages/ (3 entries):"
        assert result.lines[1].line == "frontend 1/1"

    def test_interaction_with_numeric_range_compression_range_line_is_left_alone(self) -> None:
        """A numeric_range_compression range line ("101-105") has no space
        in it at all — nothing for this stage to do, composes cleanly."""
        range_line = LineMask(
            line="101-105", decision=Decision.KEEP, reason="merged range", stage="numeric_range_compression"
        )
        table_row = LineMask(line="frontend      1/1")
        mask = ContentMask((range_line, table_row))
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].line == "101-105"
        assert result.lines[1].line == "frontend 1/1"

    def test_interaction_with_relative_timestamp_compression_cleans_up_residual_padding(self) -> None:
        """relative_timestamp_compression only strips the leading timestamp
        token — any padding *after* it in the same line is untouched by
        that stage. Running column_padding_compression afterward (this
        stage's documented recommended ordering) cleans up exactly that
        residual padding, a genuine composition, not just a no-op
        adjacency."""
        delta_line = LineMask(
            line="+1s   INFO   Loading plugins",
            decision=Decision.KEEP,
            reason="relative timestamp: +1s since previous line",
            stage="relative_timestamp_compression",
        )
        mask = ContentMask((delta_line,))
        result = self.stage.apply(mask, self._config(patterns=[r"^\+"]))
        assert result.render() == "+1s INFO Loading plugins"


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

    # -- QB-096: import-block collapsing, via the real stage/ContentMask
    # path. Shared grouping/rendering/cost-gate logic is exercised directly
    # in tests/unit/test_ast_summarize.py; these tests prove it composes
    # correctly with this stage's own body-compression and
    # preserve_patterns handling.

    def test_import_block_collapsed_and_body_compressed_together(self) -> None:
        source = (
            "import os\nimport sys\nimport json\nimport pathlib\nimport tempfile\n"
            "import shutil\nimport subprocess\nimport logging\nimport asyncio\n"
            "import numpy as np\n\n\ndef main():\n    print(os.getcwd())\n"
        )
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        rendered = result.render()
        assert rendered.startswith("Imports (10)")
        assert "Standard library:" in rendered
        assert "Third-party:\n- numpy as np" in rendered
        assert "import os" not in rendered
        assert "def main():" in rendered
        assert "print(os.getcwd())" not in rendered

    def test_import_replacement_line_count_unchanged(self) -> None:
        """The QB-096 exception documented in mask.py: no new LineMask is
        inserted for import collapsing (unlike path_prefix_fold's header
        insertion) — total line count must stay exactly the same."""
        source = "\n".join(f"import module_{i}" for i in range(15)) + "\n"
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert "Imports (15)" in result.render()
        assert len(result.lines) == len(mask.lines)

    def test_preserve_pattern_on_import_line_prevents_its_run_from_folding(self) -> None:
        source = "\n".join(f"import module_{i}" for i in range(15)) + "\n"
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["module_7"])
        result = self.stage.apply(mask, config)
        rendered = result.render()
        assert "import module_7" in rendered
        assert "Imports (" not in rendered  # whole run left unfolded
        protected = [lm for lm in result.lines if lm.decision is Decision.PROTECT]
        assert len(protected) == 1
        assert protected[0].line == "import module_7"

    def test_small_import_block_stays_unchanged(self) -> None:
        source = "import os\nimport sys\n\n\ndef f():\n    return 1\n"
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[0].line == "import os"
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].line == "import sys"
        assert result.lines[1].decision is Decision.KEEP


# ---------------------------------------------------------------------------
# code_ast_summarize (QB-005B — generic, multi-language parser framework)
#
# Framework-level tests (registry routing, analyze_python correctness in
# isolation) live in tests/unit/test_ast_summarize.py, mirroring how
# test_extract.py is separate from any stage's own test class. This class
# tests the StageHandler itself.
# ---------------------------------------------------------------------------


class TestCodeAstSummarize:
    """The new generic StageHandler. Not wired into any built-in filter yet
    (QB-005C/QB-005D's job) — tested directly, the same way
    quor/pipeline/extract's framework pieces were tested directly in
    QB-007E1 before any real handler existed."""

    stage = CodeAstSummarizeStage()

    def _config(
        self, language: str = "python", preserve: list[str] | None = None
    ) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language=language,
            preserve_patterns=preserve or [],
        )

    def test_empty_input(self) -> None:
        result = self.stage.apply(ContentMask.from_text(""), self._config())
        assert result.render() == ""

    def test_wrong_config_type_raises(self) -> None:
        with pytest.raises(TypeError, match="CodeAstSummarizeConfig"):
            self.stage.apply(ContentMask.from_text("x = 1"), RemoveAnsiConfig(type="remove_ansi"))

    def test_unsupported_language_fails_open_mask_unchanged(self) -> None:
        """QB-005A Section 4.2's 'unsupported language' case: no analyzer
        registered for `language` -> the mask is returned completely
        unchanged, silently, no exception. See code_ast_summarize.py's
        module docstring for why this lives in apply() rather than
        can_handle() (the StageHandler Protocol's can_handle() has no
        access to StageConfig)."""
        source = "def f():\n    return 1\n"
        mask = ContentMask.from_text(source)
        config = self._config(language="cobol")
        result = self.stage.apply(mask, config)
        assert result.render() == source
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_syntax_error_propagates_for_engine_fail_open(self) -> None:
        mask = ContentMask.from_text("def broken(:\n    pass\n")
        with pytest.raises(SyntaxError):
            self.stage.apply(mask, self._config(language="python"))

    def test_syntax_error_via_pipeline_fails_open_to_original(self) -> None:
        source = "def broken(:\n    pass\n"
        mask = ContentMask.from_text(source)
        entry = StageEntry(handler=self.stage, config=self._config(language="python"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = Pipeline([entry]).execute(mask)
        assert result.mask.render() == source
        assert any("code_ast_summarize" in str(w.message) for w in caught)

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = "def foo():\n    CRITICAL_MARKER = True\n    return 1\n"
        mask = ContentMask.from_text(source)
        config = self._config(language="python", preserve=["CRITICAL_MARKER"])
        result = self.stage.apply(mask, config)
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[2].decision is Decision.COMPRESS

    def test_import_block_collapsed_qb096(self) -> None:
        source = "\n".join(f"import module_{i}" for i in range(15)) + "\n"
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config(language="python"))
        assert "Imports (15)" in result.render()
        assert len(result.lines) == len(mask.lines)

    # -- Equivalence with python_ast_summarize --------------------------
    #
    # code_ast_summarize(language="python") and PythonAstSummarizeStage both
    # delegate to the exact same quor.pipeline.ast_summarize.python.analyze_python
    # via the exact same registry lookup (see both stages' module docstrings).
    # These fixtures mirror TestPythonAstSummarize's own to prove the two
    # stages produce byte-for-byte identical decisions on Python input,
    # which is the concrete proof that QB-005B introduced one shared
    # implementation, not a second, divergent one.

    @pytest.mark.parametrize(
        "source",
        [
            "def add(x, y):\n"
            '    """Add two numbers."""\n'
            "    total = x + y\n"
            "    return total\n",
            "import os\n\nDEFAULT_TIMEOUT = 30\n\n\ndef run():\n    do_work()\n    return True\n",
            "class Foo:\n"
            "    @staticmethod\n"
            "    @cached\n"
            "    def bar(x):\n"
            '        """Bar docstring."""\n'
            "        y = x * 2\n"
            "        return y\n",
            "async def fetch(url):\n"
            '    """Fetch a URL."""\n'
            "    response = await client.get(url)\n"
            "    return response\n",
            "def f(): return 1\nx = f()\n",
            'def f():\n    """Just a docstring."""\n',
        ],
    )
    def test_identical_decisions_to_python_ast_summarize_stage(self, source: str) -> None:
        python_stage = PythonAstSummarizeStage()
        python_result = python_stage.apply(
            ContentMask.from_text(source),
            PythonAstSummarizeConfig(type="python_ast_summarize"),
        )
        generic_result = self.stage.apply(
            ContentMask.from_text(source), self._config(language="python")
        )
        assert len(python_result.lines) == len(generic_result.lines)
        for python_lm, generic_lm in zip(python_result.lines, generic_result.lines, strict=True):
            assert python_lm.line == generic_lm.line
            assert python_lm.decision is generic_lm.decision


class TestCodeAstSummarizeJavaScript:
    """code_ast_summarize(language="javascript") — QB-005C, via the real
    stage/ContentMask path rather than calling analyze_javascript() directly
    (see tests/unit/test_ast_summarize.py::TestAnalyzeJavaScript for the
    analyzer-level battery). Not wired into any built-in filter's Python
    class the way python_ast_summarize is — cat-javascript.toml
    (quor/filters/builtin/) is what actually wires this stage up for real
    use; see its own inline [[filter.tests]] for filter-level coverage."""

    stage = CodeAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language="javascript",
            preserve_patterns=preserve or [],
        )

    def test_function_body_compressed_signature_preserved(self) -> None:
        source = "function add(x, y) {\n  return x + y;\n}\n"
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # function add(x, y) {
        assert result.lines[1].decision is Decision.COMPRESS  # return x + y;
        assert result.lines[2].decision is Decision.KEEP  # }

    def test_class_extends_and_method_signatures_preserved(self) -> None:
        source = (
            "class Widget extends Base {\n"
            "  constructor(x) {\n"
            "    this.x = x;\n"
            "  }\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # class Widget extends Base {
        assert result.lines[1].decision is Decision.KEEP  # constructor(x) {
        assert result.lines[2].decision is Decision.COMPRESS  # this.x = x;
        assert result.lines[3].decision is Decision.KEEP  # }
        assert result.lines[4].decision is Decision.KEEP  # }

    def test_syntax_error_propagates_for_engine_fail_open(self) -> None:
        """Unlike Python's ast.parse(), tree-sitter itself does not raise
        on malformed input (QB-005A Section 4.1) — but a genuinely
        unparseable byte sequence, or an environment/parser-level failure,
        must still propagate rather than being silently swallowed here.
        Verified via the missing-dependency path, which does raise cleanly
        through the normal exception mechanism when forced past its own
        internal warn-and-return-empty-set handling by patching the
        analyzer directly (mirrors TestRegistryFailOpenContract's "fake"
        analyzer pattern in test_ast_summarize.py)."""

        def _raises(source: str) -> set[int]:
            raise ValueError("simulated tree-sitter internal error")

        with patch.dict(ast_registry._ANALYZERS, {"javascript": _raises}):
            mask = ContentMask.from_text("function f() {\n  return 1;\n}\n")
            with pytest.raises(ValueError, match="simulated tree-sitter internal error"):
                self.stage.apply(mask, self._config())

    def test_error_node_overlap_excludes_only_the_broken_function(self) -> None:
        source = (
            "function good1(x) {\n"
            "  return x + 1;\n"
            "}\n"
            "\n"
            "function alsoBroken(y) {\n"
            "  return y +++ * ;\n"
            "}\n"
            "\n"
            "function good2(z) {\n"
            "  return z + 2;\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[1].decision is Decision.COMPRESS  # good1 body
        assert result.lines[5].decision is Decision.KEEP  # alsoBroken body: untouched
        assert result.lines[9].decision is Decision.COMPRESS  # good2 body

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = "function foo() {\n  const CRITICAL_MARKER = true;\n  return 1;\n}\n"
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["CRITICAL_MARKER"])
        result = self.stage.apply(mask, config)
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[2].decision is Decision.COMPRESS

    def test_kept_lines_are_byte_identical_to_source(self) -> None:
        """No rewriting/reformatting ever happens — mirrors
        TestPythonAstSummarize::test_kept_lines_are_byte_identical_to_source."""
        source = (
            'import { foo } from "bar";\n'
            "\n"
            "const CONST = 1;\n"
            "\n"
            "/**\n"
            " * Process data.\n"
            " */\n"
            "function process(data) {\n"
            "  const result = [];\n"
            "  for (const item of data) {\n"
            "    result.push(item);\n"
            "  }\n"
            "  return result;\n"
            "}\n"
        )
        original_lines = source.split("\n")
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        for idx, lm in enumerate(result.lines):
            if lm.decision is not Decision.COMPRESS:
                assert lm.line == original_lines[idx], f"line {idx} was modified"

    def test_import_block_collapsed_qb096(self) -> None:
        source = (
            'import {A, B, C, D} from "./foo";\n'
            'import {E, F} from "./bar";\n'
            'import Default from "./baz";\n'
            'import * as ns from "./qux";\n'
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        rendered = result.render()
        assert "./foo:\n- A\n- B\n- C\n- D" in rendered
        assert "./bar: E, F" in rendered
        assert "* as ns" in rendered
        assert len(result.lines) == len(ContentMask.from_text(source).lines)


class TestCodeAstSummarizeGo:
    """code_ast_summarize(language="go") — QB-046, via the real
    stage/ContentMask path rather than calling analyze_go() directly (see
    tests/unit/test_ast_summarize.py::TestAnalyzeGo for the analyzer-level
    battery). Not wired into any built-in filter's Python class the way
    python_ast_summarize is — cat-go.toml (quor/filters/builtin/) is what
    actually wires this stage up for real use; see its own inline
    [[filter.tests]] for filter-level coverage."""

    stage = CodeAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language="go",
            preserve_patterns=preserve or [],
        )

    def test_function_body_compressed_signature_preserved(self) -> None:
        source = "func Add(x, y int) int {\n  return x + y\n}\n"
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # func Add(x, y int) int {
        assert result.lines[1].decision is Decision.COMPRESS  # return x + y
        assert result.lines[2].decision is Decision.KEEP  # }

    def test_method_receiver_and_struct_signatures_preserved(self) -> None:
        source = (
            "type Widget struct {\n"
            "\tX int\n"
            "}\n"
            "\n"
            "func (w *Widget) Render() string {\n"
            '\treturn "hi"\n'
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # type Widget struct {
        assert result.lines[1].decision is Decision.KEEP  # X int
        assert result.lines[4].decision is Decision.KEEP  # func (w *Widget) Render() string {
        assert result.lines[5].decision is Decision.COMPRESS  # return "hi"
        assert result.lines[6].decision is Decision.KEEP  # }

    def test_syntax_error_propagates_for_engine_fail_open(self) -> None:
        """Mirrors TestCodeAstSummarizeJavaScript's identical test — see
        its own docstring for the full reasoning."""

        def _raises(source: str) -> set[int]:
            raise ValueError("simulated tree-sitter internal error")

        with patch.dict(ast_registry._ANALYZERS, {"go": _raises}):
            mask = ContentMask.from_text("func f() {\n  return 1\n}\n")
            with pytest.raises(ValueError, match="simulated tree-sitter internal error"):
                self.stage.apply(mask, self._config())

    def test_error_node_overlap_excludes_only_the_broken_function(self) -> None:
        source = (
            "func good1(x int) int {\n"
            "  return x + 1\n"
            "}\n"
            "\n"
            "func alsoBroken(y int) int {\n"
            "  return y +++ * \n"
            "}\n"
            "\n"
            "func good2(z int) int {\n"
            "  return z + 2\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[1].decision is Decision.COMPRESS  # good1 body
        assert result.lines[5].decision is Decision.KEEP  # alsoBroken body: untouched
        assert result.lines[9].decision is Decision.COMPRESS  # good2 body

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = "func foo() {\n  criticalMarker := true\n  return\n}\n"
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["criticalMarker"])
        result = self.stage.apply(mask, config)
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[2].decision is Decision.COMPRESS

    def test_kept_lines_are_byte_identical_to_source(self) -> None:
        """No rewriting/reformatting ever happens — mirrors
        TestCodeAstSummarizeJavaScript's identical test."""
        source = (
            "// Process transforms data.\n"
            "func Process(data []string) []string {\n"
            "\tresult := []string{}\n"
            "\tfor _, item := range data {\n"
            "\t\tresult = append(result, item)\n"
            "\t}\n"
            "\treturn result\n"
            "}\n"
        )
        original_lines = source.split("\n")
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        for idx, lm in enumerate(result.lines):
            if lm.decision is not Decision.COMPRESS:
                assert lm.line == original_lines[idx], f"line {idx} was modified"


class TestCodeAstSummarizeJava:
    """code_ast_summarize(language="java") — QB-046, via the real
    stage/ContentMask path rather than calling analyze_java() directly (see
    tests/unit/test_ast_summarize.py::TestAnalyzeJava for the
    analyzer-level battery). Not wired into any built-in filter's Python
    class the way python_ast_summarize is — cat-java.toml
    (quor/filters/builtin/) is what actually wires this stage up for real
    use; see its own inline [[filter.tests]] for filter-level coverage."""

    stage = CodeAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language="java",
            preserve_patterns=preserve or [],
        )

    def test_method_body_compressed_signature_preserved(self) -> None:
        source = "public class Foo {\n  public int add(int x, int y) {\n    return x + y;\n  }\n}\n"
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # public class Foo {
        assert result.lines[1].decision is Decision.KEEP  # public int add(int x, int y) {
        assert result.lines[2].decision is Decision.COMPRESS  # return x + y;
        assert result.lines[3].decision is Decision.KEEP  # }

    def test_class_extends_implements_and_constructor_preserved(self) -> None:
        source = (
            "public class Widget extends Base implements Runnable {\n"
            "  public Widget(int x) {\n"
            "    this.x = x;\n"
            "  }\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # public class Widget extends Base implements Runnable {
        assert result.lines[1].decision is Decision.KEEP  # public Widget(int x) {
        assert result.lines[2].decision is Decision.COMPRESS  # this.x = x;
        assert result.lines[3].decision is Decision.KEEP  # }
        assert result.lines[4].decision is Decision.KEEP  # }

    def test_syntax_error_propagates_for_engine_fail_open(self) -> None:
        """Mirrors TestCodeAstSummarizeGo's identical test — see its own
        docstring for the full reasoning."""

        def _raises(source: str) -> set[int]:
            raise ValueError("simulated tree-sitter internal error")

        with patch.dict(ast_registry._ANALYZERS, {"java": _raises}):
            mask = ContentMask.from_text("public class Foo {\n  public void f() {\n    return;\n  }\n}\n")
            with pytest.raises(ValueError, match="simulated tree-sitter internal error"):
                self.stage.apply(mask, self._config())

    def test_error_node_overlap_excludes_only_the_broken_method(self) -> None:
        source = (
            "public class Foo {\n"
            "  public int good1(int x) {\n"
            "    return x + 1;\n"
            "  }\n"
            "\n"
            "  public int alsoBroken(int y) {\n"
            "    return y +++ * ;\n"
            "  }\n"
            "\n"
            "  public int good2(int z) {\n"
            "    return z + 2;\n"
            "  }\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[2].decision is Decision.COMPRESS  # good1 body
        assert result.lines[6].decision is Decision.KEEP  # alsoBroken body: untouched
        assert result.lines[10].decision is Decision.COMPRESS  # good2 body

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = "public class Foo {\n  public void foo() {\n    boolean criticalMarker = true;\n    return;\n  }\n}\n"
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["criticalMarker"])
        result = self.stage.apply(mask, config)
        assert result.lines[2].decision is Decision.PROTECT
        assert result.lines[3].decision is Decision.COMPRESS

    def test_kept_lines_are_byte_identical_to_source(self) -> None:
        """No rewriting/reformatting ever happens — mirrors
        TestCodeAstSummarizeGo's identical test."""
        source = (
            "/**\n"
            " * Process transforms data.\n"
            " */\n"
            "public class Processor {\n"
            "  public List<String> process(List<String> data) {\n"
            "    List<String> result = new ArrayList<>();\n"
            "    for (String item : data) {\n"
            "      result.add(item);\n"
            "    }\n"
            "    return result;\n"
            "  }\n"
            "}\n"
        )
        original_lines = source.split("\n")
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        for idx, lm in enumerate(result.lines):
            if lm.decision is not Decision.COMPRESS:
                assert lm.line == original_lines[idx], f"line {idx} was modified"

    def test_import_block_collapsed_qb096(self) -> None:
        source = (
            "import java.util.List;\n"
            "import java.util.Map;\n"
            "import java.util.Set;\n"
            "import java.io.File;\n"
            "import java.io.InputStream;\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        rendered = result.render()
        assert "java.util:" in rendered
        assert "java.io:" in rendered
        assert len(result.lines) == len(ContentMask.from_text(source).lines)


class TestCodeAstSummarizeRust:
    """code_ast_summarize(language="rust") — QB-046, via the real
    stage/ContentMask path rather than calling analyze_rust() directly (see
    tests/unit/test_ast_summarize.py::TestAnalyzeRust for the
    analyzer-level battery). Not wired into any built-in filter's Python
    class the way python_ast_summarize is — cat-rust.toml
    (quor/filters/builtin/) is what actually wires this stage up for real
    use; see its own inline [[filter.tests]] for filter-level coverage."""

    stage = CodeAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language="rust",
            preserve_patterns=preserve or [],
        )

    def test_function_body_compressed_signature_preserved(self) -> None:
        source = "fn add(x: i32, y: i32) -> i32 {\n  return x + y;\n}\n"
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # fn add(x: i32, y: i32) -> i32 {
        assert result.lines[1].decision is Decision.COMPRESS  # return x + y;
        assert result.lines[2].decision is Decision.KEEP  # }

    def test_method_struct_and_impl_header_preserved(self) -> None:
        source = (
            "struct Widget {\n"
            "    x: i32,\n"
            "}\n"
            "\n"
            "impl Widget {\n"
            "    fn render(&self) -> String {\n"
            '        String::from("hi")\n'
            "    }\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # struct Widget {
        assert result.lines[1].decision is Decision.KEEP  # x: i32,
        assert result.lines[4].decision is Decision.KEEP  # impl Widget {
        assert result.lines[5].decision is Decision.KEEP  # fn render(&self) -> String {
        assert result.lines[6].decision is Decision.COMPRESS  # String::from("hi")
        assert result.lines[7].decision is Decision.KEEP  # }

    def test_syntax_error_propagates_for_engine_fail_open(self) -> None:
        """Mirrors TestCodeAstSummarizeGo's identical test — see its own
        docstring for the full reasoning."""

        def _raises(source: str) -> set[int]:
            raise ValueError("simulated tree-sitter internal error")

        with patch.dict(ast_registry._ANALYZERS, {"rust": _raises}):
            mask = ContentMask.from_text("fn f() {\n  return 1;\n}\n")
            with pytest.raises(ValueError, match="simulated tree-sitter internal error"):
                self.stage.apply(mask, self._config())

    def test_error_node_overlap_excludes_only_the_broken_function(self) -> None:
        source = (
            "fn good1(x: i32) -> i32 {\n"
            "  return x + 1;\n"
            "}\n"
            "\n"
            "fn also_broken(y: i32) -> i32 {\n"
            "  return y +++ * ;\n"
            "}\n"
            "\n"
            "fn good2(z: i32) -> i32 {\n"
            "  return z + 2;\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[1].decision is Decision.COMPRESS  # good1 body
        assert result.lines[5].decision is Decision.KEEP  # also_broken body: untouched
        assert result.lines[9].decision is Decision.COMPRESS  # good2 body

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = "fn foo() {\n  let critical_marker = true;\n  return;\n}\n"
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["critical_marker"])
        result = self.stage.apply(mask, config)
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[2].decision is Decision.COMPRESS

    def test_kept_lines_are_byte_identical_to_source(self) -> None:
        """No rewriting/reformatting ever happens — mirrors
        TestCodeAstSummarizeGo's identical test."""
        source = (
            "/// Processes transforms data.\n"
            "fn process(data: Vec<String>) -> Vec<String> {\n"
            "    let mut result = Vec::new();\n"
            "    for item in data {\n"
            "        result.push(item);\n"
            "    }\n"
            "    result\n"
            "}\n"
        )
        original_lines = source.split("\n")
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        for idx, lm in enumerate(result.lines):
            if lm.decision is not Decision.COMPRESS:
                assert lm.line == original_lines[idx], f"line {idx} was modified"


class TestCodeAstSummarizeCSharp:
    """code_ast_summarize(language="csharp") — QB-046, via the real
    stage/ContentMask path rather than calling analyze_csharp() directly
    (see tests/unit/test_ast_summarize.py::TestAnalyzeCSharp for the
    analyzer-level battery). Not wired into any built-in filter's Python
    class the way python_ast_summarize is — cat-csharp.toml
    (quor/filters/builtin/) is what actually wires this stage up for real
    use; see its own inline [[filter.tests]] for filter-level coverage."""

    stage = CodeAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language="csharp",
            preserve_patterns=preserve or [],
        )

    def test_method_body_compressed_signature_preserved(self) -> None:
        source = "public class Foo\n{\n  public int Add(int x, int y)\n  {\n    return x + y;\n  }\n}\n"
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # public class Foo
        assert result.lines[2].decision is Decision.KEEP  # public int Add(int x, int y)
        assert result.lines[4].decision is Decision.COMPRESS  # return x + y;
        assert result.lines[5].decision is Decision.KEEP  # }

    def test_class_base_list_and_constructor_preserved(self) -> None:
        source = (
            "public class Widget : Base, IRunnable\n"
            "{\n"
            "  public Widget(int x)\n"
            "  {\n"
            "    this.x = x;\n"
            "  }\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP  # public class Widget : Base, IRunnable
        assert result.lines[2].decision is Decision.KEEP  # public Widget(int x)
        assert result.lines[4].decision is Decision.COMPRESS  # this.x = x;
        assert result.lines[5].decision is Decision.KEEP  # }
        assert result.lines[6].decision is Decision.KEEP  # }

    def test_syntax_error_propagates_for_engine_fail_open(self) -> None:
        """Mirrors TestCodeAstSummarizeGo's identical test — see its own
        docstring for the full reasoning."""

        def _raises(source: str) -> set[int]:
            raise ValueError("simulated tree-sitter internal error")

        with patch.dict(ast_registry._ANALYZERS, {"csharp": _raises}):
            mask = ContentMask.from_text("public class Foo\n{\n  public void F()\n  {\n    return;\n  }\n}\n")
            with pytest.raises(ValueError, match="simulated tree-sitter internal error"):
                self.stage.apply(mask, self._config())

    def test_error_node_overlap_excludes_only_the_broken_method(self) -> None:
        """Uses a `$` token, not a malformed-but-legal-tokens expression —
        see tests/unit/test_ast_summarize.py::TestAnalyzeCSharp's identical
        test for why a `$` reliably produces a genuine `ERROR` node in this
        grammar where some other malformed expressions do not."""
        source = (
            "public class Foo\n"
            "{\n"
            "  public int Good1(int x)\n"
            "  {\n"
            "    return x + 1;\n"
            "  }\n"
            "\n"
            "  public int AlsoBroken(int y)\n"
            "  {\n"
            "    return y $ y;\n"
            "  }\n"
            "\n"
            "  public int Good2(int z)\n"
            "  {\n"
            "    return z + 2;\n"
            "  }\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[4].decision is Decision.COMPRESS  # Good1 body
        assert result.lines[9].decision is Decision.KEEP  # AlsoBroken body: untouched
        assert result.lines[14].decision is Decision.COMPRESS  # Good2 body

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = (
            "public class Foo\n{\n  public void F()\n  {\n"
            "    bool criticalMarker = true;\n    return;\n  }\n}\n"
        )
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["criticalMarker"])
        result = self.stage.apply(mask, config)
        assert result.lines[4].decision is Decision.PROTECT
        assert result.lines[5].decision is Decision.COMPRESS

    def test_kept_lines_are_byte_identical_to_source(self) -> None:
        """No rewriting/reformatting ever happens — mirrors
        TestCodeAstSummarizeGo's identical test."""
        source = (
            "/// <summary>\n"
            "/// Processes transforms data.\n"
            "/// </summary>\n"
            "public class Processor\n"
            "{\n"
            "  public List<string> Process(List<string> data)\n"
            "  {\n"
            "    var result = new List<string>();\n"
            "    foreach (var item in data)\n"
            "    {\n"
            "      result.Add(item);\n"
            "    }\n"
            "    return result;\n"
            "  }\n"
            "}\n"
        )
        original_lines = source.split("\n")
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        for idx, lm in enumerate(result.lines):
            if lm.decision is not Decision.COMPRESS:
                assert lm.line == original_lines[idx], f"line {idx} was modified"


class TestCodeAstSummarizeTypeScript:
    """code_ast_summarize(language="typescript") — QB-005D, via the real
    stage/ContentMask path (see
    tests/unit/test_ast_summarize.py::TestAnalyzeTypeScript for the
    analyzer-level battery). Not wired into a TypeScript-specific stage
    class — cat-typescript.toml's `cat-typescript` block is what actually
    wires this up for real `.ts` use."""

    stage = CodeAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language="typescript",
            preserve_patterns=preserve or [],
        )

    def test_function_body_compressed_signature_preserved(self) -> None:
        source = "function add(x: number, y: number): number {\n  return x + y;\n}\n"
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.KEEP

    def test_interface_type_enum_never_entered_into_compress_set(self) -> None:
        source = (
            "interface Point {\n"
            "  x: number;\n"
            "}\n"
            "\n"
            "type Alias = number;\n"
            "\n"
            "enum Color {\n"
            "  Red,\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert all(lm.decision is Decision.KEEP for lm in result.lines)

    def test_abstract_method_and_overload_signatures_preserved(self) -> None:
        source = (
            "function overload(x: number): number;\n"
            "function overload(x: any): any {\n"
            "  return x;\n"
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.KEEP
        assert result.lines[2].decision is Decision.COMPRESS
        assert result.lines[3].decision is Decision.KEEP

    def test_malformed_syntax_excludes_broken_region_without_raising(self) -> None:
        """Unlike Python's ast.parse(), tree-sitter never raises on
        malformed input — it recovers via ERROR nodes (QB-005A Section
        4.1). apply() itself must not raise here; the broken function's
        body is simply left untouched (ERROR-node-overlap exclusion),
        while a clean sibling function still compresses normally."""
        source = (
            "function good(x: number): number {\n"
            "  return x + 1;\n"
            "}\n"
            "\n"
            "function broken(: {\n"
            "  return 1;\n"
            "}\n"
        )
        mask = ContentMask.from_text(source)
        result = self.stage.apply(mask, self._config())
        assert result.lines[1].decision is Decision.COMPRESS  # good's body
        assert result.lines[5].decision is Decision.KEEP  # broken's body: untouched

    def test_preserve_pattern_protects_body_line(self) -> None:
        source = "function foo(): void {\n  const CRITICAL_MARKER = true;\n  return;\n}\n"
        mask = ContentMask.from_text(source)
        config = self._config(preserve=["CRITICAL_MARKER"])
        result = self.stage.apply(mask, config)
        assert result.lines[1].decision is Decision.PROTECT
        assert result.lines[2].decision is Decision.COMPRESS

    def test_kept_lines_are_byte_identical_to_source(self) -> None:
        source = (
            'import { foo } from "bar";\n'
            "\n"
            "interface Config {\n"
            "  timeout: number;\n"
            "}\n"
            "\n"
            "/**\n"
            " * Process data.\n"
            " */\n"
            "function process(data: string[]): string[] {\n"
            "  const result: string[] = [];\n"
            "  for (const item of data) {\n"
            "    result.push(item);\n"
            "  }\n"
            "  return result;\n"
            "}\n"
        )
        original_lines = source.split("\n")
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        for idx, lm in enumerate(result.lines):
            if lm.decision is not Decision.COMPRESS:
                assert lm.line == original_lines[idx], f"line {idx} was modified"

    def test_import_block_collapsed_qb096(self) -> None:
        source = (
            'import {A, B, C} from "./foo";\n'
            'import {D, E} from "./bar";\n'
            'import Default from "./baz";\n'
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        rendered = result.render()
        assert "./foo:" in rendered
        assert "./bar:" in rendered
        assert len(result.lines) == len(ContentMask.from_text(source).lines)


class TestCodeAstSummarizeTsx:
    """code_ast_summarize(language="tsx") — QB-005D's second TypeScript
    grammar variant, routed by cat-typescript.toml's `cat-tsx` block."""

    stage = CodeAstSummarizeStage()

    def _config(self, preserve: list[str] | None = None) -> CodeAstSummarizeConfig:
        return CodeAstSummarizeConfig(
            type="code_ast_summarize",
            language="tsx",
            preserve_patterns=preserve or [],
        )

    def test_jsx_function_body_compressed(self) -> None:
        source = (
            "function Widget(props: { label: string }): JSX.Element {\n"
            '  return <div className="box">{props.label}</div>;\n'
            "}\n"
        )
        result = self.stage.apply(ContentMask.from_text(source), self._config())
        assert result.lines[0].decision is Decision.KEEP
        assert result.lines[1].decision is Decision.COMPRESS
        assert result.lines[2].decision is Decision.KEEP

    def test_typescript_and_tsx_are_genuinely_different_registrations(self) -> None:
        """Routing `.ts` content through language="tsx" and vice versa must
        not silently succeed as if they were interchangeable — this test
        proves the two config values reach two different analyzer
        functions by observing a real behavioral difference: JSX content
        compresses under "tsx" but is excluded (ERROR-node overlap) under
        "typescript"."""
        jsx_source = "function Widget(): JSX.Element {\n  return <div />;\n}\n"
        tsx_result = self.stage.apply(
            ContentMask.from_text(jsx_source), self._config()
        )
        ts_config = CodeAstSummarizeConfig(type="code_ast_summarize", language="typescript")
        ts_result = self.stage.apply(ContentMask.from_text(jsx_source), ts_config)
        assert tsx_result.lines[1].decision is Decision.COMPRESS
        assert ts_result.lines[1].decision is Decision.KEEP
