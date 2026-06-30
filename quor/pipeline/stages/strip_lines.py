"""strip_lines stage: COMPRESS matching lines, PROTECT preserved lines.

Decision precedence (highest wins):
  1. Already PROTECT  → never touched
  2. matches preserve_patterns → PROTECT (no subsequent stage can downgrade)
  3. matches patterns           → COMPRESS
  4. no match                   → leave decision unchanged

Patterns use the `regex` package so user-supplied patterns cannot cause
catastrophic backtracking to hang the process (timeout=1.0 s per match).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig


class StripLinesConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: list[str] = Field(default_factory=list)


class StripLinesStage:
    """Compress lines matching patterns; protect lines matching preserve_patterns."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "strip_lines"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, StripLinesConfig):
            raise TypeError(
                f"strip_lines requires StripLinesConfig, got {type(config).__name__}"
            )

        compiled_strip = [_compile(p) for p in config.patterns]
        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        new_lines: list[LineMask] = []

        for lm in mask.lines:
            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                continue

            if lm.decision is not Decision.COMPRESS and compiled_strip and matches_any(
                lm.line, compiled_strip
            ):
                new_lines.append(
                    LineMask(lm.line, Decision.COMPRESS, "matches strip pattern", self.stage_type)
                )
                continue

            new_lines.append(lm)

        return ContentMask(tuple(new_lines))
