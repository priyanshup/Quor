"""QB-036: Pipeline-level early exit (optimization only).

Covers the engine-level skip predicate (`_mask_fully_decided`,
`_remaining_stages_are_skippable`), `Pipeline.execute()`'s `early_exit`
switch, `FilterRegistry.apply()`/`trace()`'s split (optimization on for the
real compression path, off for `quor explain`'s diagnostic trace), the exact
safety cases the implementation is conservative about (a later stage with
`preserve_patterns` set, `match_output`, and unrecognized/plugin stage
types), and the core correctness property this whole feature exists to
guarantee: `apply()`'s rendered output is always byte-for-byte identical
whether or not early exit actually fires.

See tests/unit/test_pipeline.py for the pre-existing engine test suite this
file must not regress (early exit never triggers for that file's synthetic
stage types, since none of them use a real, allowlisted `stage_type` string
— see this file's own module docstring in quor/pipeline/engine.py for why
that is the deliberate, conservative design, not an oversight).
"""

from __future__ import annotations

import warnings
from typing import ClassVar
from unittest.mock import patch

from quor.filters.registry import FilterRegistry
from quor.pipeline.engine import (
    Pipeline,
    StageEntry,
    _mask_fully_decided,
    _remaining_stages_are_skippable,
)
from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages.base import StageConfig
from quor.pipeline.stages.max_tokens import MaxTokensConfig, MaxTokensStage
from quor.pipeline.stages.strip_lines import StripLinesConfig, StripLinesStage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(stage_type: str = "test", preserve_patterns: list[str] | None = None) -> StageConfig:
    return StageConfig(type=stage_type, preserve_patterns=preserve_patterns or [])


class _FakeCompressAllStage:
    """A stage whose stage_type is a real, allowlisted string ("strip_lines")
    and whose apply() marks every KEEP line COMPRESS — used to drive a mask
    into the "fully decided" state without depending on real StripLinesStage
    pattern-matching semantics."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "strip_lines"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        new_lines = tuple(
            LineMask(line=lm.line, decision=Decision.COMPRESS, reason="test", stage=self.stage_type)
            if lm.decision is Decision.KEEP
            else lm
            for lm in mask.lines
        )
        return ContentMask(lines=new_lines)


class _RecordingStage:
    """Records every can_handle()/apply() call instead of raising — a stage
    that raises would have its exception silently caught by the pipeline's
    own fail-open try/except (indistinguishable from "was skipped for early
    exit" purely by looking for an escaped exception), so proving
    non-invocation requires an out-of-band call log instead."""

    api_version: ClassVar[int] = 1

    def __init__(self, stage_type: str) -> None:
        self.stage_type = stage_type
        self.calls: list[str] = []

    def can_handle(self, content: str, content_type: str) -> bool:
        self.calls.append("can_handle")
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        self.calls.append("apply")
        return mask


class _UnknownTypeNoOpStage:
    """Simulates a third-party/plugin stage: a stage_type the engine's
    allowlist has never heard of. apply() is a harmless no-op, but the
    engine must not assume that from the stage_type alone."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "my_custom_plugin_stage"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        return mask


# ---------------------------------------------------------------------------
# _mask_fully_decided / _remaining_stages_are_skippable — pure predicates
# ---------------------------------------------------------------------------


class TestMaskFullyDecided:
    def test_all_keep_is_not_fully_decided(self) -> None:
        assert _mask_fully_decided(ContentMask.from_text("a\nb")) is False

    def test_mixed_keep_and_compress_is_not_fully_decided(self) -> None:
        lines = (
            LineMask("a", Decision.COMPRESS),
            LineMask("b", Decision.KEEP),
        )
        assert _mask_fully_decided(ContentMask(lines)) is False

    def test_all_compress_is_fully_decided(self) -> None:
        lines = tuple(LineMask(ln, Decision.COMPRESS) for ln in ["a", "b"])
        assert _mask_fully_decided(ContentMask(lines)) is True

    def test_all_protect_is_fully_decided(self) -> None:
        lines = tuple(LineMask(ln, Decision.PROTECT) for ln in ["a", "b"])
        assert _mask_fully_decided(ContentMask(lines)) is True

    def test_mixed_compress_and_protect_is_fully_decided(self) -> None:
        lines = (
            LineMask("a", Decision.COMPRESS),
            LineMask("b", Decision.PROTECT),
        )
        assert _mask_fully_decided(ContentMask(lines)) is True

    def test_empty_mask_is_vacuously_fully_decided(self) -> None:
        assert _mask_fully_decided(ContentMask(())) is True


class TestRemainingStagesAreSkippable:
    def test_empty_remaining_list_is_skippable(self) -> None:
        assert _remaining_stages_are_skippable([]) is True

    def test_known_safe_type_with_no_preserve_patterns_is_skippable(self) -> None:
        entry = StageEntry(handler=_FakeCompressAllStage(), config=_config())
        assert _remaining_stages_are_skippable([entry]) is True

    def test_known_safe_type_with_preserve_patterns_is_not_skippable(self) -> None:
        """The documented, deliberate conservatism: group_repeated/max_tokens/
        remove_ansi can promote an already-COMPRESS line back to PROTECT via
        preserve_patterns (see engine.py's module docstring) — so ANY
        remaining stage with preserve_patterns set blocks the skip, even a
        stage type otherwise considered safe."""
        entry = StageEntry(
            handler=_FakeCompressAllStage(), config=_config(preserve_patterns=["FIXME"])
        )
        assert _remaining_stages_are_skippable([entry]) is False

    def test_match_output_is_never_skippable(self) -> None:
        handler = _RecordingStage("match_output")
        entry = StageEntry(handler=handler, config=_config())
        assert _remaining_stages_are_skippable([entry]) is False

    def test_unknown_stage_type_is_not_skippable(self) -> None:
        entry = StageEntry(handler=_UnknownTypeNoOpStage(), config=_config())
        assert _remaining_stages_are_skippable([entry]) is False

    def test_all_must_be_skippable_not_just_one(self) -> None:
        safe = StageEntry(handler=_FakeCompressAllStage(), config=_config())
        unsafe = StageEntry(handler=_UnknownTypeNoOpStage(), config=_config())
        assert _remaining_stages_are_skippable([safe, unsafe]) is False
        assert _remaining_stages_are_skippable([unsafe, safe]) is False


# ---------------------------------------------------------------------------
# Pipeline.execute() — early exit actually skipping stage invocation
# ---------------------------------------------------------------------------


class TestPipelineEarlyExitTriggers:
    def test_trailing_safe_stage_is_skipped_without_being_called(self) -> None:
        mask = ContentMask.from_text("a\nb\nc")
        trailing = _RecordingStage("max_tokens")
        entries = [
            StageEntry(handler=_FakeCompressAllStage(), config=_config()),
            StageEntry(handler=trailing, config=_config()),
        ]
        result = Pipeline(entries).execute(mask)

        assert result.mask.render() == ""
        assert trailing.calls == []
        assert len(result.stage_results) == 2
        assert result.stage_results[0].was_skipped is False
        assert result.stage_results[1].was_skipped is True
        assert "early exit" in result.stage_results[1].skip_reason

    def test_multiple_trailing_stages_all_skipped(self) -> None:
        mask = ContentMask.from_text("a\nb")
        trailing_stages = [
            _RecordingStage("deduplicate_consecutive"),
            _RecordingStage("max_tokens"),
            _RecordingStage("regex_replace"),
        ]
        entries = [StageEntry(handler=_FakeCompressAllStage(), config=_config())] + [
            StageEntry(handler=s, config=_config()) for s in trailing_stages
        ]
        result = Pipeline(entries).execute(mask)

        assert all(s.calls == [] for s in trailing_stages)
        assert len(result.stage_results) == 4
        assert [r.was_skipped for r in result.stage_results] == [False, True, True, True]

    def test_early_exit_false_forces_every_stage_to_run(self) -> None:
        """The exact opt-out FilterRegistry.trace() relies on."""
        mask = ContentMask.from_text("a\nb")
        trailing = _RecordingStage("max_tokens")
        entries = [
            StageEntry(handler=_FakeCompressAllStage(), config=_config()),
            StageEntry(handler=trailing, config=_config()),
        ]
        result = Pipeline(entries).execute(mask, early_exit=False)
        assert trailing.calls == ["can_handle", "apply"]
        assert result.stage_results[1].was_skipped is False

    def test_stage_results_length_always_equals_entry_count(self) -> None:
        """Mirrors test_pipeline.py's own test_stages_run_in_order invariant
        — early exit must never change how many StageResult entries come
        back, only whether each one reflects a real run or a skip."""
        mask = ContentMask.from_text("a\nb")
        entries = [
            StageEntry(handler=_FakeCompressAllStage(), config=_config()),
            StageEntry(handler=_RecordingStage("max_tokens"), config=_config()),
            StageEntry(handler=_RecordingStage("strip_lines"), config=_config()),
        ]
        result = Pipeline(entries).execute(mask)
        assert len(result.stage_results) == len(entries)


class TestPipelineEarlyExitDoesNotTrigger:
    def test_keep_lines_remaining_prevents_skip(self) -> None:
        """A stage that does NOT fully compress the mask must not trigger a
        skip of the next stage — real work remains."""
        mask = ContentMask.from_text("a\nb")

        class _PartialCompress:
            api_version: ClassVar[int] = 1
            stage_type: ClassVar[str] = "strip_lines"

            def can_handle(self, content: str, content_type: str) -> bool:
                return True

            def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
                lines = list(mask.lines)
                lines[0] = LineMask(lines[0].line, Decision.COMPRESS, "test", self.stage_type)
                return ContentMask(tuple(lines))

        trailing = _RecordingStage("max_tokens")
        entries = [
            StageEntry(handler=_PartialCompress(), config=_config()),
            StageEntry(handler=trailing, config=_config()),
        ]
        result = Pipeline(entries).execute(mask)
        assert trailing.calls == ["can_handle", "apply"]
        assert result.stage_results[1].was_skipped is False

    def test_preserve_patterns_on_remaining_stage_prevents_skip(self) -> None:
        mask = ContentMask.from_text("a\nb")
        trailing = _RecordingStage("max_tokens")
        entries = [
            StageEntry(handler=_FakeCompressAllStage(), config=_config()),
            StageEntry(handler=trailing, config=_config(preserve_patterns=["FIXME"])),
        ]
        result = Pipeline(entries).execute(mask)
        assert trailing.calls == ["can_handle", "apply"]
        assert result.stage_results[1].was_skipped is False

    def test_match_output_as_remaining_stage_prevents_skip(self) -> None:
        mask = ContentMask.from_text("a\nb")
        trailing = _RecordingStage("match_output")
        entries = [
            StageEntry(handler=_FakeCompressAllStage(), config=_config()),
            StageEntry(handler=trailing, config=_config()),
        ]
        result = Pipeline(entries).execute(mask)
        assert trailing.calls == ["can_handle", "apply"]
        assert result.stage_results[1].was_skipped is False

    def test_unknown_plugin_stage_type_prevents_skip(self) -> None:
        """A stage_type outside the hand-audited allowlist (third-party
        plugin, file:// stage) must always actually run — the engine cannot
        vouch for code it has never read."""
        mask = ContentMask.from_text("a\nb")
        entries = [
            StageEntry(handler=_FakeCompressAllStage(), config=_config()),
            StageEntry(handler=_UnknownTypeNoOpStage(), config=_config()),
        ]
        result = Pipeline(entries).execute(mask)
        assert result.stage_results[1].was_skipped is False

    def test_no_early_exit_when_entries_list_is_empty(self) -> None:
        mask = ContentMask.from_text("a\nb")
        result = Pipeline([]).execute(mask)
        assert result.stage_results == ()
        assert result.mask.render() == "a\nb"


# ---------------------------------------------------------------------------
# Fail-open: a broken early-exit check must never break execution
# ---------------------------------------------------------------------------


class TestEarlyExitFailsOpen:
    def test_exception_in_skip_predicate_falls_back_to_running_stage(self) -> None:
        mask = ContentMask.from_text("a\nb")
        trailing = _RecordingStage("max_tokens")
        entries = [
            StageEntry(handler=_FakeCompressAllStage(), config=_config()),
            StageEntry(handler=trailing, config=_config()),
        ]

        with (
            patch(
                "quor.pipeline.engine._remaining_stages_are_skippable",
                side_effect=RuntimeError("synthetic predicate failure"),
            ),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = Pipeline(entries).execute(mask)

        assert trailing.calls == [
            "can_handle",
            "apply",
        ], "a broken optimization must fall back to real execution"
        assert result.stage_results[1].was_skipped is False
        assert any("Early-exit check failed" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# FilterRegistry integration — apply() gets the optimization, trace() doesn't
# ---------------------------------------------------------------------------


class TestFilterRegistryEarlyExitIntegration:
    registry = FilterRegistry(skip_user=True, skip_project=True)

    def _apply_with_flag(self, filter_config: object, content: str, *, early_exit: bool) -> str:
        """Reimplements FilterRegistry.apply()'s exact abort_unless/abort_if/
        on_empty logic (unmodified, untouched by this task) so the ONLY
        variable between the two calls being compared is early_exit itself —
        calling apply() directly for one side and _run_pipeline() for the
        other would also let the abort_unless/abort_if short-circuit (which
        never even reaches the pipeline) confound the comparison."""
        if filter_config.abort_unless and not any(  # type: ignore[attr-defined]
            s in content for s in filter_config.abort_unless  # type: ignore[attr-defined]
        ):
            return content
        if filter_config.abort_if and any(  # type: ignore[attr-defined]
            s in content for s in filter_config.abort_if  # type: ignore[attr-defined]
        ):
            return content
        rendered = self.registry._run_pipeline(
            filter_config, content, early_exit=early_exit
        ).mask.render()
        if not rendered.strip() and filter_config.on_empty:  # type: ignore[attr-defined]
            return filter_config.on_empty  # type: ignore[attr-defined]
        return rendered

    def test_apply_output_identical_with_and_without_early_exit(self) -> None:
        """The core correctness property: forcing early_exit off must never
        change apply()'s rendered output for any built-in filter's own
        inline test inputs — the whole point of this optimization is that
        it is unobservable in the render()'d string."""
        for _, filter_config in self.registry.all_filters():
            for test in filter_config.tests:
                with_opt = self._apply_with_flag(filter_config, test.input, early_exit=True)
                without_opt = self._apply_with_flag(filter_config, test.input, early_exit=False)
                assert with_opt == without_opt, (
                    f"{filter_config.name!r} test {test.description!r}: "
                    "early exit changed observable output"
                )
                # And with_opt must equal the real apply() output too — a
                # sanity check that _apply_with_flag(early_exit=True)
                # faithfully mirrors apply() itself, not a separate
                # reimplementation that happens to agree with itself.
                assert with_opt == self.registry.apply(filter_config, test.input)

    def test_trace_never_skips_for_early_exit(self) -> None:
        """quor explain's diagnostic trace always shows every stage's real
        per-stage result, even for a filter/input combination where apply()
        itself would genuinely trigger early exit."""
        filter_config = self.registry.find("cat script.py")
        assert filter_config is not None
        content = "def f():\n    pass\n# comment\n"
        trace = self.registry.trace(filter_config, content)
        for r in trace.stage_results:
            assert "early exit" not in r.skip_reason

    def test_cat_python_trailing_max_tokens_actually_skipped(self) -> None:
        """A concrete, realistic early-exit trigger: a Python "file" that is
        nothing but ordinary `#` comments (no function bodies at all, so
        python_ast_summarize finds nothing to do) — strip_lines' comment
        patterns compress every single line, leaving max_tokens (the last
        stage, no preserve_patterns configured in cat-python.toml) with zero
        KEEP lines to do anything to. Real cat-python.toml filter, real
        stages — not a synthetic StageHandler."""
        filter_config = self.registry.find("cat tiny.py")
        assert filter_config is not None
        assert filter_config.name == "cat-python"
        content = "# comment one\n# comment two\n# comment three"

        with patch.object(MaxTokensStage, "apply") as mock_apply:
            rendered = self.registry.apply(filter_config, content)

        mock_apply.assert_not_called()
        assert rendered == ""
        # Confirm this is genuinely early exit, not a can_handle()=False skip
        # or coincidence: forcing early_exit off must produce the identical
        # render() and must call max_tokens for real. autospec=True (not
        # wraps=MaxTokensStage.apply) is required here so the mock correctly
        # binds `self` when accessed through an instance.
        with patch.object(
            MaxTokensStage, "apply", autospec=True, side_effect=MaxTokensStage.apply
        ) as spy:
            forced = self.registry._run_pipeline(
                filter_config, content, early_exit=False
            ).mask.render()
        spy.assert_called()
        assert rendered == forced


# ---------------------------------------------------------------------------
# Direct stage sanity — the real strip_lines/max_tokens are indeed in the
# allowlist and behave as the module docstring's audit claims (regression
# guard: if either stage's preserve_patterns handling ever changes to also
# reconsider already-COMPRESS lines, this suite's conservatism assumption
# should be revisited, not silently invalidated).
# ---------------------------------------------------------------------------


class TestAuditedStageAssumptionsStillHold:
    def test_strip_lines_never_reconsiders_already_compressed_lines(self) -> None:
        lines = (LineMask("FIXME: x", Decision.COMPRESS, "prior stage", "prior"),)
        mask = ContentMask(lines)
        config = StripLinesConfig(type="strip_lines", preserve_patterns=["FIXME"])
        result = StripLinesStage().apply(mask, config)
        assert result.lines[0].decision is Decision.COMPRESS

    def test_max_tokens_preserve_patterns_can_upgrade_already_compressed_line(self) -> None:
        """Documents the exact quirk engine.py's module docstring describes
        — this is EXISTING, pre-QB-036 stage behavior (not something this
        task introduced or fixed), and is precisely why max_tokens is only
        ever treated as skippable when ITS OWN preserve_patterns is empty."""
        lines = (LineMask("FIXME: x", Decision.COMPRESS, "prior stage", "prior"),)
        mask = ContentMask(lines)
        config = MaxTokensConfig(type="max_tokens", limit=1000, preserve_patterns=["FIXME"])
        result = MaxTokensStage().apply(mask, config)
        assert result.lines[0].decision is Decision.PROTECT
