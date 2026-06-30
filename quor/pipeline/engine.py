"""Pipeline executor.

Runs a sequence of StageHandler instances against a ContentMask, enforcing:
  1. PROTECT immutability — no stage may downgrade a PROTECT decision.
  2. Fail-open — a stage that raises is skipped with a warning; the pipeline
     continues with the mask unchanged.

The engine does NOT know about filter configs or content detection.
Callers are responsible for building the stage list and detecting content type.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages.base import StageConfig, StageHandler, StageResult


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
    ) -> PipelineResult:
        """Run all stages in order and return the final mask with a trace.

        Args:
            mask:          Initial ContentMask (from ContentMask.from_text).
            raw_content:   The original subprocess output string, passed to
                           can_handle() so stages can inspect raw content.
            content_type:  Detected content type string (e.g. "diff", "json").
        """
        results: list[StageResult] = []

        for entry in self._entries:
            before_compressed = sum(
                1 for lm in mask.lines if lm.decision is Decision.COMPRESS
            )

            try:
                if not entry.handler.can_handle(raw_content, content_type):
                    results.append(
                        StageResult(
                            stage_type=entry.handler.stage_type,
                            lines_before=len(mask.lines),
                            lines_compressed=0,
                            was_skipped=True,
                            skip_reason="can_handle returned False",
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
