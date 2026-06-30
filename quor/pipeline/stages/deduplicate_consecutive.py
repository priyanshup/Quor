"""deduplicate_consecutive stage: COMPRESS adjacent duplicate lines.

Tracks the last *kept* (KEEP or PROTECT) line's content. If the next KEEP
line has identical content, it is COMPRESSED. Already-COMPRESS lines are
passed through unchanged and do not update the "last kept" tracker.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig


class DeduplicateConsecutiveConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeduplicateConsecutiveStage:
    """Compress consecutive duplicate KEEP lines, keeping the first occurrence."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "deduplicate_consecutive"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, DeduplicateConsecutiveConfig):
            raise TypeError(
                f"deduplicate_consecutive requires DeduplicateConsecutiveConfig, "
                f"got {type(config).__name__}"
            )

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        new_lines: list[LineMask] = []
        last_kept: str | None = None

        for lm in mask.lines:
            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                last_kept = lm.line
                continue

            if lm.decision is Decision.COMPRESS:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                last_kept = lm.line
                continue

            if last_kept is not None and lm.line == last_kept:
                new_lines.append(
                    LineMask(lm.line, Decision.COMPRESS, "consecutive duplicate", self.stage_type)
                )
            else:
                new_lines.append(lm)
                last_kept = lm.line

        return ContentMask(tuple(new_lines))
