"""Minimal NoOpTestStage for Quor plugin loader integration tests."""

from __future__ import annotations

from typing import ClassVar

from quor.pipeline.mask import ContentMask
from quor.pipeline.stages.base import StageConfig


class NoOpTestStage:
    """Pass-through stage that makes no changes. For testing plugin discovery only."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "noop_test"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        return mask
