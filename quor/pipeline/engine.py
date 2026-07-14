"""Pipeline executor.

Runs a sequence of StageHandler instances against a ContentMask, enforcing:
  1. PROTECT immutability — no stage may downgrade a PROTECT decision.
  2. Fail-open — a stage that raises is skipped with a warning; the pipeline
     continues with the mask unchanged.

The engine does NOT know about filter configs or content detection.
Callers are responsible for building the stage list and detecting content type.

QB-036 — early exit (optimization only, see "Early exit" section below).
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages.base import StageConfig, StageHandler, StageResult
from quor.tracking.db import count_tokens

# ---------------------------------------------------------------------------
# Early exit (QB-036)
#
# Once every line in the mask already has a final Decision (COMPRESS or
# PROTECT — i.e. no KEEP line remains), render() cannot change no matter what
# a *well-behaved* stage does to it from that point on: every built-in stage
# only ever assigns new decisions to KEEP lines (COMPRESS/PROTECT are always
# read, never lines the stage's own "main" pass reclassifies). So once the
# mask is "fully decided," any suffix of *known-safe* remaining stages can be
# skipped outright without running them, with render() guaranteed identical
# to what actually running them would have produced.
#
# "Known-safe" is deliberately a hand-audited allowlist of built-in
# `stage_type` strings, not a blanket rule — reading every built-in stage's
# `apply()` for this task surfaced one real, pre-existing subtlety worth
# recording plainly: Decision.COMPRESS is NOT engine-enforced immutable the
# way PROTECT is (see `_enforce_protect` below — it only restores PROTECT).
# Three built-in stages — `group_repeated`, `max_tokens`, `remove_ansi` —
# apply their own `preserve_patterns` pass with a condition of
# `decision is not Decision.PROTECT` (not "and not Decision.COMPRESS"),
# so *if* one of them is configured with `preserve_patterns` and a pattern
# happens to match a line an earlier stage already marked COMPRESS, that
# line is promoted back to PROTECT — becoming visible in render() again.
# (`strip_lines`, `deduplicate_consecutive`, `truncate_lines`,
# `regex_replace`, `python_ast_summarize`, and `code_ast_summarize` all
# correctly exclude already-COMPRESS lines before considering
# `preserve_patterns`; only those three don't.) No built-in filter shipped
# today actually configures `preserve_patterns` on anything but
# `strip_lines` (verified across every `quor/filters/builtin/*.toml`), so
# this never fires in practice — but the engine cannot assume that stays
# true for a project/user filter it has never seen. Rather than relying on
# "this doesn't happen today," the skip predicate below is conservative:
# it additionally requires that stage's *own* `preserve_patterns` be empty,
# which is both necessary (given the quirk above) and sufficient (verified
# by reading every remaining built-in stage's `apply()` in full).
#
# `match_output` is excluded from the allowlist entirely, unconditionally —
# its own module docstring already calls it "the highest-risk stage in the
# pipeline": whether it fires depends on the *entire current render() text*
# matching an arbitrary regex, not on any per-line Decision, so no
# Decision-based reasoning (fully-decided mask or not) can safely predict
# its effect without actually running it.
#
# This is the only place `stage_type` strings appear inside the otherwise
# stage-agnostic engine. That is a deliberate, narrow exception, not a
# precedent for the engine generally knowing about specific stages — see
# backlog.md's QB-036 entry for the full reasoning. No new StageHandler
# field or Protocol change was introduced for this: the allowlist reuses
# `StageHandler.stage_type` (already required) and `StageConfig.
# preserve_patterns` (already a base-class field every stage config
# inherits), so zero stage implementations needed to change.
# ---------------------------------------------------------------------------

_STAGE_TYPES_INERT_ON_DECIDED_LINES: frozenset[str] = frozenset(
    {
        "remove_ansi",
        "strip_lines",
        "deduplicate_consecutive",
        "group_repeated",
        "max_tokens",
        "truncate_lines",
        "regex_replace",
        "python_ast_summarize",
        "code_ast_summarize",
        # "match_output" is deliberately NOT included — see module docstring.
    }
)

_EARLY_EXIT_SKIP_REASON = (
    "early exit: ContentMask fully decided (no KEEP lines remain); "
    "remaining stages cannot change output"
)


def _mask_fully_decided(mask: ContentMask) -> bool:
    """True if no line in `mask` still has Decision.KEEP.

    A line that is already COMPRESS or PROTECT is exactly the set of lines
    no *known-safe* remaining stage (see module docstring) can affect
    further — this is the trigger condition for early exit.
    """
    return all(lm.decision is not Decision.KEEP for lm in mask.lines)


def _remaining_stages_are_skippable(entries: Sequence[StageEntry]) -> bool:
    """True if every entry in `entries` is a known-safe stage_type whose own
    `preserve_patterns` is empty — see module docstring for the full
    correctness argument (why both conditions are required)."""
    return all(
        entry.handler.stage_type in _STAGE_TYPES_INERT_ON_DECIDED_LINES
        and not entry.config.preserve_patterns
        for entry in entries
    )


@dataclass(frozen=True)
class StageEntry:
    """A (handler, config) pair consumed by the pipeline."""

    handler: StageHandler
    config: StageConfig


@dataclass(frozen=True)
class PipelineResult:
    """Final mask plus per-stage execution trace."""

    mask: ContentMask
    stage_results: tuple[StageResult, ...]

    @property
    def total_compressed(self) -> int:
        return sum(r.lines_compressed for r in self.stage_results)

    @property
    def total_before(self) -> int:
        return self.stage_results[0].lines_before if self.stage_results else len(self.mask.lines)


class Pipeline:
    """Executes a fixed list of stages in order against a ContentMask."""

    def __init__(self, entries: list[StageEntry]) -> None:
        self._entries = list(entries)

    def execute(
        self,
        mask: ContentMask,
        raw_content: str = "",
        content_type: str = "text",
        *,
        early_exit: bool = True,
        track_tokens: bool = False,
    ) -> PipelineResult:
        """Run all stages in order and return the final mask with a trace.

        Args:
            mask:          Initial ContentMask (from ContentMask.from_text).
            raw_content:   The original subprocess output string, passed to
                           can_handle() so stages can inspect raw content.
            content_type:  Detected content type string (e.g. "diff", "json").
            early_exit:    QB-036 optimization switch (default on). When the
                           mask becomes "fully decided" (no KEEP lines left)
                           and every remaining, not-yet-run stage is a
                           known-safe type (see module docstring), the rest
                           of the pipeline is skipped without being invoked —
                           `mask.render()` is guaranteed identical to running
                           them for real. Every skipped stage still gets a
                           `StageResult` entry (`was_skipped=True`), so
                           `len(stage_results) == len(self._entries)` always
                           holds, exactly as it already does for a
                           `can_handle()`-False or raising stage. Set False
                           to force every stage to actually run — used by
                           `FilterRegistry.trace()` (`quor explain`) so its
                           stage-by-stage diagnostic view is completely
                           unaffected by this optimization.
            track_tokens:  QB-039 analytics switch (default off). When True,
                           each StageResult's tokens_before/tokens_after is
                           populated by rendering the mask (mask.render() +
                           count_tokens()) immediately before and after that
                           stage runs. Default False means zero extra
                           rendering/counting work and StageResult.
                           tokens_before/tokens_after stay None — this is a
                           purely opt-in measurement path, never used by the
                           real Bash/Read hook dispatch or `apply()`.
        """
        results: list[StageResult] = []
        entries = self._entries
        i = 0
        n = len(entries)

        while i < n:
            try:
                can_exit_early = (
                    early_exit
                    and _mask_fully_decided(mask)
                    and _remaining_stages_are_skippable(entries[i:])
                )
            except Exception as exc:  # noqa: BLE001 — the optimization itself must fail open
                warnings.warn(
                    f"[quor] Early-exit check failed, running remaining stages normally: {exc}",
                    stacklevel=2,
                )
                can_exit_early = False

            if can_exit_early:
                lines_before = len(mask.lines)
                # The mask cannot change for any of these entries (that's the
                # whole early-exit guarantee), so one render/count covers all
                # of them instead of one per remaining stage.
                unchanged_tokens = count_tokens(mask.render()) if track_tokens else None
                for remaining in entries[i:]:
                    results.append(
                        StageResult(
                            stage_type=remaining.handler.stage_type,
                            lines_before=lines_before,
                            lines_compressed=0,
                            was_skipped=True,
                            skip_reason=_EARLY_EXIT_SKIP_REASON,
                            tokens_before=unchanged_tokens,
                            tokens_after=unchanged_tokens,
                        )
                    )
                break

            entry = entries[i]
            i += 1
            before_compressed = sum(
                1 for lm in mask.lines if lm.decision is Decision.COMPRESS
            )
            stage_tokens_before = count_tokens(mask.render()) if track_tokens else None

            try:
                if not entry.handler.can_handle(raw_content, content_type):
                    results.append(
                        StageResult(
                            stage_type=entry.handler.stage_type,
                            lines_before=len(mask.lines),
                            lines_compressed=0,
                            was_skipped=True,
                            skip_reason="can_handle returned False",
                            tokens_before=stage_tokens_before,
                            tokens_after=stage_tokens_before,
                        )
                    )
                    continue

                new_mask = entry.handler.apply(mask, entry.config)
                new_mask = _enforce_protect(mask, new_mask)

                after_compressed = sum(
                    1 for lm in new_mask.lines if lm.decision is Decision.COMPRESS
                )
                newly_compressed = max(0, after_compressed - before_compressed)

                mask = new_mask
                results.append(
                    StageResult(
                        stage_type=entry.handler.stage_type,
                        lines_before=len(mask.lines),
                        lines_compressed=newly_compressed,
                        tokens_before=stage_tokens_before,
                        tokens_after=count_tokens(mask.render()) if track_tokens else None,
                    )
                )

            except Exception as exc:  # noqa: BLE001 — stage failures are non-fatal
                warnings.warn(
                    f"[quor] Stage {entry.handler.stage_type!r} failed and was skipped: {exc}",
                    stacklevel=2,
                )
                results.append(
                    StageResult(
                        stage_type=entry.handler.stage_type,
                        lines_before=len(mask.lines),
                        lines_compressed=0,
                        was_skipped=True,
                        skip_reason="stage raised an exception",
                        error=str(exc),
                        tokens_before=stage_tokens_before,
                        tokens_after=stage_tokens_before,
                    )
                )

        return PipelineResult(mask=mask, stage_results=tuple(results))


def _enforce_protect(original: ContentMask, updated: ContentMask) -> ContentMask:
    """Restore PROTECT on any line that was PROTECT in the original mask.

    If the line counts differ (e.g. a future stage that merges lines), we
    cannot do index-based enforcement and return the updated mask as-is.
    This situation should not occur in V1 stages.
    """
    if len(original.lines) != len(updated.lines):
        return updated

    needs_fix = any(
        orig.decision is Decision.PROTECT and upd.decision is not Decision.PROTECT
        for orig, upd in zip(original.lines, updated.lines, strict=True)
    )
    if not needs_fix:
        return updated

    fixed: list[LineMask] = []
    for orig_lm, upd_lm in zip(original.lines, updated.lines, strict=True):
        if orig_lm.decision is Decision.PROTECT and upd_lm.decision is not Decision.PROTECT:
            fixed.append(
                LineMask(
                    line=upd_lm.line,
                    decision=Decision.PROTECT,
                    reason=orig_lm.reason,
                    stage=orig_lm.stage,
                )
            )
        else:
            fixed.append(upd_lm)
    return ContentMask(tuple(fixed))
