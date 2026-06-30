"""Phase 1 unit tests: ContentMask primitive, Pipeline engine, content-type detection.

Test stages defined here are minimal helpers that implement StageHandler.
They are NOT production stages — those come in Phase 2.
"""

from __future__ import annotations

import warnings
from typing import ClassVar

import pytest

from quor.pipeline.content_type import ContentType, detect
from quor.pipeline.engine import Pipeline, PipelineResult, StageEntry
from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages.base import StageConfig, StageHandler, StageResult

# ---------------------------------------------------------------------------
# Test-only stage implementations
# ---------------------------------------------------------------------------


class _CompressAllStage:
    """Marks every non-PROTECT KEEP line as COMPRESS."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "compress_all"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        new_lines = tuple(
            LineMask(line=lm.line, decision=Decision.COMPRESS, reason="test", stage="compress_all")
            if lm.decision is Decision.KEEP
            else lm
            for lm in mask.lines
        )
        return ContentMask(lines=new_lines)


class _NoOpStage:
    """Returns the mask unchanged."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "no_op"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        return mask


class _RaisingStage:
    """Always raises RuntimeError in apply()."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "raising"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        raise RuntimeError("deliberate test error")


class _SkippingStage:
    """Returns can_handle=False for any input."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "skipping"

    def can_handle(self, content: str, content_type: str) -> bool:
        return False

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:  # pragma: no cover
        raise AssertionError("apply must never be called when can_handle returns False")


class _DowngradeProtectStage:
    """Attempts to change PROTECT lines to COMPRESS (engine must prevent this)."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "downgrade_protect"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        new_lines = tuple(
            LineMask(
                line=lm.line,
                decision=Decision.COMPRESS,
                reason="attempted downgrade",
                stage="downgrade_protect",
            )
            for lm in mask.lines
        )
        return ContentMask(lines=new_lines)


def _make_config(stage_type: str = "test") -> StageConfig:
    return StageConfig(type=stage_type)


def _entry(handler: StageHandler, stage_type: str = "test") -> StageEntry:
    return StageEntry(handler=handler, config=_make_config(stage_type))


# ---------------------------------------------------------------------------
# ContentMask: from_text and render
# ---------------------------------------------------------------------------


class TestContentMaskFromText:
    def test_single_line(self) -> None:
        mask = ContentMask.from_text("hello")
        assert len(mask.lines) == 1
        assert mask.lines[0].line == "hello"
        assert mask.lines[0].decision is Decision.KEEP

    def test_multiline(self) -> None:
        mask = ContentMask.from_text("a\nb\nc")
        assert len(mask.lines) == 3
        assert [lm.line for lm in mask.lines] == ["a", "b", "c"]

    def test_empty_string(self) -> None:
        mask = ContentMask.from_text("")
        assert len(mask.lines) == 1
        assert mask.lines[0].line == ""

    def test_trailing_newline(self) -> None:
        mask = ContentMask.from_text("a\nb\n")
        assert len(mask.lines) == 3  # ["a", "b", ""]
        assert mask.lines[2].line == ""

    def test_default_decision_is_keep(self) -> None:
        mask = ContentMask.from_text("x\ny")
        assert all(lm.decision is Decision.KEEP for lm in mask.lines)


class TestContentMaskRender:
    def test_all_keep(self) -> None:
        mask = ContentMask.from_text("a\nb\nc")
        assert mask.render() == "a\nb\nc"

    def test_all_compress(self) -> None:
        lines = tuple(
            LineMask(line=ln, decision=Decision.COMPRESS) for ln in ["a", "b", "c"]
        )
        mask = ContentMask(lines=lines)
        assert mask.render() == ""

    def test_mixed_keep_compress(self) -> None:
        lines = (
            LineMask(line="keep_me", decision=Decision.KEEP),
            LineMask(line="drop_me", decision=Decision.COMPRESS),
            LineMask(line="keep_me_too", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        assert mask.render() == "keep_me\nkeep_me_too"

    def test_all_protect(self) -> None:
        lines = tuple(
            LineMask(line=ln, decision=Decision.PROTECT) for ln in ["a", "b"]
        )
        mask = ContentMask(lines=lines)
        assert mask.render() == "a\nb"

    def test_protect_survives_with_keep(self) -> None:
        lines = (
            LineMask(line="protected", decision=Decision.PROTECT),
            LineMask(line="normal", decision=Decision.KEEP),
            LineMask(line="dropped", decision=Decision.COMPRESS),
        )
        mask = ContentMask(lines=lines)
        assert mask.render() == "protected\nnormal"

    def test_render_roundtrip(self) -> None:
        text = "line one\nline two\nline three"
        assert ContentMask.from_text(text).render() == text


class TestContentMaskStats:
    def test_all_keep(self) -> None:
        mask = ContentMask.from_text("a\nb")
        s = mask.stats()
        assert s["total"] == 2
        assert s["kept"] == 2
        assert s["compressed"] == 0
        assert s["protected"] == 0

    def test_mixed(self) -> None:
        lines = (
            LineMask(line="k", decision=Decision.KEEP),
            LineMask(line="c", decision=Decision.COMPRESS),
            LineMask(line="p", decision=Decision.PROTECT),
        )
        s = ContentMask(lines=lines).stats()
        assert s == {"total": 3, "kept": 1, "compressed": 1, "protected": 1}


# ---------------------------------------------------------------------------
# LineMask: frozen invariant
# ---------------------------------------------------------------------------


class TestLineMaskFrozen:
    def test_lineMask_is_frozen(self) -> None:
        lm = LineMask(line="x")
        with pytest.raises((AttributeError, TypeError)):
            lm.line = "y"  # type: ignore[misc]

    def test_contentMask_is_frozen(self) -> None:
        mask = ContentMask.from_text("a")
        with pytest.raises((AttributeError, TypeError)):
            mask.lines = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Pipeline: stage execution and fail-open behaviour
# ---------------------------------------------------------------------------


class TestPipelineNoStages:
    def test_empty_pipeline_returns_mask_unchanged(self) -> None:
        mask = ContentMask.from_text("a\nb")
        result = Pipeline([]).execute(mask)
        assert result.mask.render() == "a\nb"
        assert result.stage_results == ()
        assert result.total_compressed == 0


class TestPipelineStageRunOrder:
    def test_single_stage_runs(self) -> None:
        mask = ContentMask.from_text("a\nb")
        result = Pipeline([_entry(_CompressAllStage())]).execute(mask)
        assert result.mask.render() == ""
        assert len(result.stage_results) == 1
        assert result.stage_results[0].stage_type == "compress_all"

    def test_no_op_stage_leaves_mask_unchanged(self) -> None:
        mask = ContentMask.from_text("x\ny")
        result = Pipeline([_entry(_NoOpStage())]).execute(mask)
        assert result.mask.render() == "x\ny"

    def test_stages_run_in_order(self) -> None:
        """Compress-all followed by no-op: result is empty (compress already applied)."""
        mask = ContentMask.from_text("a\nb")
        result = Pipeline(
            [_entry(_CompressAllStage()), _entry(_NoOpStage())]
        ).execute(mask)
        assert result.mask.render() == ""
        assert len(result.stage_results) == 2


class TestPipelineFailOpen:
    def test_raising_stage_is_skipped(self) -> None:
        mask = ContentMask.from_text("a\nb")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = Pipeline([_entry(_RaisingStage())]).execute(mask)

        assert result.mask.render() == "a\nb"
        assert len(result.stage_results) == 1
        sr = result.stage_results[0]
        assert sr.was_skipped is True
        assert sr.error == "deliberate test error"
        assert any("raising" in str(w.message) for w in caught)

    def test_subsequent_stages_run_after_failure(self) -> None:
        """Raising stage fails → no-op continues → compress-all runs."""
        mask = ContentMask.from_text("a\nb")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = Pipeline(
                [_entry(_RaisingStage()), _entry(_NoOpStage()), _entry(_CompressAllStage())]
            ).execute(mask)

        assert result.mask.render() == ""
        assert len(result.stage_results) == 3
        assert result.stage_results[0].was_skipped is True
        assert result.stage_results[1].was_skipped is False
        assert result.stage_results[2].was_skipped is False

    def test_skipping_stage_does_not_call_apply(self) -> None:
        mask = ContentMask.from_text("a")
        result = Pipeline([_entry(_SkippingStage())]).execute(mask)
        assert result.stage_results[0].was_skipped is True
        assert result.stage_results[0].skip_reason == "can_handle returned False"
        assert result.mask.render() == "a"


class TestPipelineProtectEnforcement:
    def test_protect_decision_survives_compress_all(self) -> None:
        """A PROTECT line must survive even when a stage marks everything COMPRESS."""
        lines = (
            LineMask(line="must_keep", decision=Decision.PROTECT, reason="error line", stage="preserve"),
            LineMask(line="can_drop", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)
        result = Pipeline([_entry(_CompressAllStage())]).execute(mask)
        rendered = result.mask.render()
        assert "must_keep" in rendered
        assert "can_drop" not in rendered

    def test_protect_restored_by_engine_not_stage(self) -> None:
        """The engine restores PROTECT on lines a stage tried to downgrade."""
        lines = (
            LineMask(line="protected", decision=Decision.PROTECT, reason="p", stage="s"),
            LineMask(line="normal", decision=Decision.KEEP),
        )
        mask = ContentMask(lines=lines)

        result = Pipeline([_entry(_DowngradeProtectStage())]).execute(mask)

        # The engine enforces PROTECT — both lines should survive render
        rendered_lines = result.mask.render().split("\n")
        assert "protected" in rendered_lines

        # The restored LineMask must have PROTECT decision
        first_lm = result.mask.lines[0]
        assert first_lm.decision is Decision.PROTECT

    def test_protect_survives_multiple_stages(self) -> None:
        """PROTECT must survive N stages that all try to downgrade it."""
        lines = (
            LineMask(line="anchor", decision=Decision.PROTECT, reason="r", stage="s"),
        )
        mask = ContentMask(lines=lines)
        pipeline = Pipeline(
            [_entry(_DowngradeProtectStage()), _entry(_DowngradeProtectStage())]
        )
        result = pipeline.execute(mask)
        assert result.mask.lines[0].decision is Decision.PROTECT
        assert "anchor" in result.mask.render()

    def test_stage_result_counts_newly_compressed_lines(self) -> None:
        mask = ContentMask.from_text("a\nb\nc")
        result = Pipeline([_entry(_CompressAllStage())]).execute(mask)
        assert result.stage_results[0].lines_compressed == 3
        assert result.total_compressed == 3


class TestPipelineResultProperties:
    def test_total_before_with_stages(self) -> None:
        mask = ContentMask.from_text("a\nb")
        result = Pipeline([_entry(_NoOpStage())]).execute(mask)
        assert result.total_before == 2

    def test_total_before_without_stages(self) -> None:
        mask = ContentMask.from_text("a\nb")
        result = Pipeline([]).execute(mask)
        assert result.total_before == 2


# ---------------------------------------------------------------------------
# StageHandler Protocol: runtime_checkable isinstance check
# ---------------------------------------------------------------------------


class TestStageHandlerProtocol:
    def test_valid_stage_satisfies_protocol(self) -> None:
        assert isinstance(_CompressAllStage(), StageHandler)

    def test_arbitrary_object_does_not_satisfy_protocol(self) -> None:
        assert not isinstance(object(), StageHandler)


# ---------------------------------------------------------------------------
# StageResult properties
# ---------------------------------------------------------------------------


class TestStageResult:
    def test_lines_after_property(self) -> None:
        sr = StageResult(stage_type="x", lines_before=10, lines_compressed=3)
        assert sr.lines_after == 7

    def test_skipped_result(self) -> None:
        sr = StageResult(
            stage_type="x",
            lines_before=5,
            lines_compressed=0,
            was_skipped=True,
            skip_reason="can_handle returned False",
        )
        assert sr.lines_after == 5
        assert sr.was_skipped is True


# ---------------------------------------------------------------------------
# Content-type detection
# ---------------------------------------------------------------------------

_JSON_FIXTURE = '{"status": "ok", "count": 42}'
_JSON_ARRAY_FIXTURE = '[{"id": 1}, {"id": 2}]'
_DIFF_FIXTURE = """\
diff --git a/quor/mask.py b/quor/mask.py
index abc1234..def5678 100644
--- a/quor/mask.py
+++ b/quor/mask.py
@@ -1,4 +1,5 @@
 import sys
-old_line = True
+new_line = True
+extra_line = False
"""
_TRACEBACK_FIXTURE = """\
Traceback (most recent call last):
  File "quor/pipeline/engine.py", line 42, in execute
    result = stage.apply(mask, config)
  File "quor/pipeline/stages/strip_lines.py", line 18, in apply
    raise ValueError("bad pattern")
ValueError: bad pattern
"""
_ANSI_FIXTURE = "\n".join(
    [
        "\x1b[32mPASSED\x1b[0m tests/unit/test_foo.py::test_bar",
        "\x1b[32mPASSED\x1b[0m tests/unit/test_foo.py::test_baz",
        "\x1b[31mFAILED\x1b[0m tests/unit/test_foo.py::test_qux",
        "\x1b[0m",
        "\x1b[1m1 failed, 2 passed in 0.34s\x1b[0m",
    ]
)
_PLAIN_FIXTURE = """\
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
"""
_PYTEST_OUTPUT_FIXTURE = """\
============================= test session starts ==============================
platform win32 -- Python 3.14.0, pytest-9.1.1
collected 3 items

tests/unit/test_foo.py::test_bar PASSED
tests/unit/test_foo.py::test_baz PASSED
tests/unit/test_foo.py::test_qux FAILED

=================================== FAILURES ===================================
_________________________ test_qux _________________________
AssertionError: got False
"""


class TestContentTypeDetect:
    def test_json_object(self) -> None:
        assert detect(_JSON_FIXTURE) is ContentType.JSON

    def test_json_array(self) -> None:
        assert detect(_JSON_ARRAY_FIXTURE) is ContentType.JSON

    def test_diff(self) -> None:
        assert detect(_DIFF_FIXTURE) is ContentType.DIFF

    def test_python_traceback(self) -> None:
        assert detect(_TRACEBACK_FIXTURE) is ContentType.TRACEBACK

    def test_ansi_heavy(self) -> None:
        assert detect(_ANSI_FIXTURE) is ContentType.ANSI_HEAVY

    def test_plain_text(self) -> None:
        assert detect(_PLAIN_FIXTURE) is ContentType.PLAIN_TEXT

    def test_pytest_output_is_plain_text_by_default(self) -> None:
        # pytest output doesn't have ANSI codes here — falls through to plain text
        assert detect(_PYTEST_OUTPUT_FIXTURE) is ContentType.PLAIN_TEXT

    def test_empty_string_is_plain_text(self) -> None:
        assert detect("") is ContentType.PLAIN_TEXT

    def test_whitespace_only_is_plain_text(self) -> None:
        assert detect("   \n\n\t  ") is ContentType.PLAIN_TEXT

    def test_invalid_json_prefix_falls_through(self) -> None:
        # Starts with '{' but is not valid JSON → falls through to plain text
        result = detect("{not valid json at all")
        assert result is not ContentType.JSON

    def test_traceback_takes_priority_over_ansi(self) -> None:
        # Traceback with ANSI codes should be detected as TRACEBACK
        content = "\x1b[31mTraceback (most recent call last):\x1b[0m\n  File x.py\nValueError"
        assert detect(content) is ContentType.TRACEBACK
