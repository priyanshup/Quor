"""max_tokens stage: COMPRESS lines beyond an estimated token budget.

Token estimation: ceil(len(line) / 4) — the same char/4 approximation used
throughout Quor. This is labeled as an estimate (±20%) everywhere it appears.

Strategies:
  head  — keep the first N tokens of output (compress the tail)
  tail  — keep the last N tokens of output (compress the head)
  both  — keep the first N//2 and last N//2 tokens (compress the middle)

PROTECT lines are counted toward the total but never compressed. If PROTECT
lines alone exceed the budget, all KEEP lines are compressed (nothing we
can do about PROTECT lines — they are absolute).
"""

from __future__ import annotations

import math
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig


class MaxTokensConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(gt=0, description="Maximum estimated tokens in rendered output.")
    strategy: Literal["head", "tail", "both"] = "tail"


class MaxTokensStage:
    """Compress lines that push the output over the estimated token budget."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "max_tokens"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, MaxTokensConfig):
            raise TypeError(
                f"max_tokens requires MaxTokensConfig, got {type(config).__name__}"
            )

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        lines = list(mask.lines)

        # Apply preserve_patterns first
        if compiled_preserve:
            lines = [
                LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                if lm.decision is not Decision.PROTECT and matches_any(lm.line, compiled_preserve)
                else lm
                for lm in lines
            ]

        # Check current token usage (only non-COMPRESS lines count)
        current_tokens = sum(
            _line_tokens(lm.line) for lm in lines if lm.decision is not Decision.COMPRESS
        )
        if current_tokens <= config.limit:
            return ContentMask(tuple(lines))

        # Determine which indices to keep
        if config.strategy == "head":
            keep = _head_indices(lines, config.limit)
        elif config.strategy == "tail":
            keep = _tail_indices(lines, config.limit)
        else:
            half = max(1, config.limit // 2)
            keep = _head_indices(lines, half) | _tail_indices(lines, half)

        new_lines: list[LineMask] = []
        for idx, lm in enumerate(lines):
            if lm.decision in (Decision.PROTECT, Decision.COMPRESS) or idx in keep:
                new_lines.append(lm)
            else:
                new_lines.append(
                    LineMask(lm.line, Decision.COMPRESS, "beyond token budget", self.stage_type)
                )

        return ContentMask(tuple(new_lines))


def _line_tokens(line: str) -> int:
    return max(1, math.ceil(len(line) / 4))


def _head_indices(lines: list[LineMask], limit: int) -> set[int]:
    """Indices of KEEP lines to keep using head strategy."""
    keep: set[int] = set()
    budget = limit
    for i, lm in enumerate(lines):
        if lm.decision is Decision.PROTECT:
            keep.add(i)
            continue
        if lm.decision is Decision.COMPRESS:
            continue
        cost = _line_tokens(lm.line)
        if budget >= cost:
            budget -= cost
            keep.add(i)
    return keep


def _tail_indices(lines: list[LineMask], limit: int) -> set[int]:
    """Indices of KEEP lines to keep using tail strategy."""
    keep: set[int] = set()
    budget = limit
    for i in range(len(lines) - 1, -1, -1):
        lm = lines[i]
        if lm.decision is Decision.PROTECT:
            keep.add(i)
            continue
        if lm.decision is Decision.COMPRESS:
            continue
        cost = _line_tokens(lm.line)
        if budget >= cost:
            budget -= cost
            keep.add(i)
    return keep
