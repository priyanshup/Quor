"""Base types for the ContentMask stage system.

StageHandler: the Protocol every stage (built-in and plugin) must implement.
StageConfig:  Pydantic v2 base config; concrete stages subclass and add fields.
StageResult:  per-stage execution summary consumed by quor explain / tracking.

Plugin contract (stable after V1.0):
  - api_version must equal 1 for the current API.
  - can_handle must return False (not raise) when content is unsuitable.
  - apply must return a ContentMask; it must never mutate its input.
  - apply must never call network APIs or read from the user's home directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from quor.pipeline.mask import ContentMask


class StageConfig(BaseModel):
    """Base configuration for a pipeline stage.

    Concrete stage configs inherit from this and add their own fields.
    extra="allow" lets the base class accept unknown keys so the filter
    loader can pass raw TOML dicts before dispatching to the right subclass.
    Concrete subclasses should use extra="forbid".
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str
    preserve_patterns: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class StageResult:
    """Summary of what one stage did during pipeline execution.

    `tokens_before`/`tokens_after` (QB-039 analytics) are `None` unless the
    pipeline ran with `track_tokens=True` (see `quor.pipeline.engine.
    Pipeline.execute`) — the default, hot-path run (real Bash/Read hooks,
    `apply()`) never sets them, so this is a purely additive, opt-in
    measurement, not a change to what any stage computes.
    """

    stage_type: str
    lines_before: int
    lines_compressed: int
    was_skipped: bool = False
    skip_reason: str = ""
    error: str = ""
    tokens_before: int | None = None
    tokens_after: int | None = None

    @property
    def lines_after(self) -> int:
        return self.lines_before - self.lines_compressed

    @property
    def tokens_saved(self) -> int | None:
        """`None` when tokens weren't tracked for this run (see class docstring)."""
        if self.tokens_before is None or self.tokens_after is None:
            return None
        return self.tokens_before - self.tokens_after

    @property
    def compression_pct(self) -> float | None:
        """`None` when tokens weren't tracked, or `tokens_before` was 0."""
        saved = self.tokens_saved
        if saved is None or not self.tokens_before:
            return None
        return saved / self.tokens_before * 100


@runtime_checkable
class StageHandler(Protocol):
    """Protocol that every compression stage must implement.

    Both built-in stages and third-party plugins must satisfy this protocol.
    Compliance is validated at plugin registration time via isinstance().

    Class attributes (must be declared at class level, not instance level):
      api_version: int  — must equal 1 for the current API
      stage_type: str   — unique identifier used in traces and TOML configs
    """

    api_version: ClassVar[int]
    stage_type: ClassVar[str]

    def can_handle(self, content: str, content_type: str) -> bool:
        """Return False to skip this stage cleanly for this content.

        Returning False is not an error — the stage is simply skipped.
        Raising an exception here is treated as a stage failure.
        """
        ...

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        """Apply this stage to the mask and return an updated mask.

        Rules:
        - Never mutate the input mask.
        - Never downgrade a PROTECT decision (the engine enforces this, but
          stages should not rely on the engine to fix their mistakes).
        - Return a ContentMask with the same number of lines as the input
          unless the stage explicitly modifies line count (group_repeated only).
        """
        ...
