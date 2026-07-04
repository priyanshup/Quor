"""truncate_lines stage: cap individual line length without changing line count.

Long lines (stack traces, JSON payloads, long paths) can dominate token cost
even when the number of lines is otherwise under control. This stage shortens
any KEEP line longer than `max_length` characters, appending `marker` so the
truncation itself is visible rather than silently changing the line's meaning.

PROTECT lines are never touched — same invariant as every other stage
(preserve_patterns matches take precedence over truncation, exactly like
strip_lines/max_tokens). Already-COMPRESS lines are left untouched. Only line
*content* changes; the number of LineMask entries is always identical to the
input, and no Decision besides PROTECT (via preserve_patterns) is ever set.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig


class TruncateLinesConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_length: int = Field(
        gt=0,
        description="Maximum characters per line before truncation.",
    )
    marker: str = Field(
        default="…[truncated]",
        description="Appended to a truncated line so the cut is visible, not silent.",
    )


class TruncateLinesStage:
    """Truncate KEEP lines longer than max_length; PROTECT lines are exempt."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "truncate_lines"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, TruncateLinesConfig):
            raise TypeError(
                f"truncate_lines requires TruncateLinesConfig, got {type(config).__name__}"
            )

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        new_lines: list[LineMask] = []

        for lm in mask.lines:
            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                continue

            if lm.decision is Decision.COMPRESS:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                continue

            if len(lm.line) > config.max_length:
                truncated = _truncate(lm.line, config.max_length, config.marker)
                new_lines.append(
                    LineMask(
                        truncated,
                        Decision.KEEP,
                        f"truncated from {len(lm.line)} to {config.max_length} chars",
                        self.stage_type,
                    )
                )
            else:
                new_lines.append(lm)

        return ContentMask(tuple(new_lines))


def _truncate(line: str, max_length: int, marker: str) -> str:
    """Cut `line` to `max_length` chars total, appending `marker` when it fits.

    If `marker` alone is as long as (or longer than) `max_length`, fall back
    to a hard cut with no marker rather than producing a line longer than
    max_length or a bare marker with no original content at all.
    """
    if len(marker) >= max_length:
        return line[:max_length]
    return line[: max_length - len(marker)] + marker
