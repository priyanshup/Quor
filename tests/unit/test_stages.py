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
