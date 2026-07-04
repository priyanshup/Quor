"""match_output stage: whole-output pattern short-circuit.

If the ENTIRE current rendered output (mask.render() — all non-COMPRESS
lines joined — at the point this stage runs) fully matches `pattern`, the
mask is collapsed to `summary` and everything else is marked COMPRESS.
Intended for whole-output shortcuts like a clean `git status` or a
successful build summary, where the remaining stages have essentially
nothing left to do afterward — that is how this avoids the cost of
downstream stage processing, without any change to the pipeline engine.

Safety (this is the highest-risk stage in the pipeline; read before editing):

- This stage refuses to fire at all if the mask currently contains any
  PROTECT line. Collapsing to a single summary line necessarily reassigns
  which LineMask occupies which index, and a naive collapse risks a
  PROTECT-restoration mismatch (Pipeline._enforce_protect can only restore a
  PROTECT *decision* at an index — it cannot recover the *original content*
  of a PROTECT line whose slot got overwritten with summary text). Refusing
  to fire whenever PROTECT is already present sidesteps that entirely,
  rather than relying on subtle index-based reasoning. In practice this
  costs nothing for the intended use cases (a clean/successful run has
  nothing that needed protecting in the first place).
- This stage keeps the same number of LineMask entries as its input (first
  entry becomes the summary, the rest are marked COMPRESS), so the engine's
  ordinary PROTECT-restoration path still applies unmodified — this stage
  does not need, and does not introduce, any special-cased engine behavior.
- Firing is explicit, opt-in TOML config only (`[[filter.stages]] type =
  "match_output"`); there is no implicit or automatic whole-output matching
  anywhere else in Quor.
- Every firing is traced exactly like any other stage via the existing
  Pipeline/StageResult mechanism (visible in `quor explain`'s Stage Trace),
  and additionally emits an explicit warning to stderr so a short-circuit is
  never a silent, hard-to-notice event.
"""

from __future__ import annotations

import warnings
from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, _fullmatch
from quor.pipeline.stages.base import StageConfig


class MatchOutputConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: str = Field(
        description="Regex matched against the ENTIRE current rendered output (fullmatch)."
    )
    summary: str = Field(
        description="Replacement text used when `pattern` matches the whole output."
    )


class MatchOutputStage:
    """Collapse the whole output to `summary` when `pattern` fully matches it."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "match_output"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, MatchOutputConfig):
            raise TypeError(
                f"match_output requires MatchOutputConfig, got {type(config).__name__}"
            )

        if not mask.lines:
            return mask

        # Never short-circuit over content that already contains protected
        # lines — see module docstring "Safety" section.
        if any(lm.decision is Decision.PROTECT for lm in mask.lines):
            return mask

        compiled = _compile(config.pattern)
        current = mask.render()

        try:
            matched = _fullmatch(compiled, current) is not None
        except TimeoutError:
            warnings.warn(
                f"[quor] Pattern {config.pattern!r} timed out in match_output; skipping",
                stacklevel=3,
            )
            return mask

        if not matched:
            return mask

        warnings.warn(
            f"[quor] match_output: whole output matched {config.pattern!r}; collapsed to summary",
            stacklevel=3,
        )

        lines = list(mask.lines)
        new_lines = [
            LineMask(config.summary, Decision.KEEP, "match_output short-circuit", self.stage_type)
        ]
        new_lines.extend(
            LineMask(lm.line, Decision.COMPRESS, "match_output short-circuit", self.stage_type)
            for lm in lines[1:]
        )
        return ContentMask(tuple(new_lines))
