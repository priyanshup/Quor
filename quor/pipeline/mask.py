"""Core ContentMask primitive.

Every pipeline stage reads a ContentMask and returns a new ContentMask.
Stages annotate lines via Decision; the final render step applies the mask.

Invariants (enforced by Pipeline.execute, not by individual stages):
- PROTECT decisions are absolute — no subsequent stage can downgrade them.
- Line content is never modified by stages that set COMPRESS/KEEP decisions.
  group_repeated and collapse_unchanged_context are the exceptions: each may
  replace one line in a collapsed run with a summary/placeholder string
  (e.g. "msg (xN)" or "... N unchanged lines omitted ...").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    KEEP = "KEEP"
    COMPRESS = "COMPRESS"
    PROTECT = "PROTECT"


@dataclass(frozen=True)
class LineMask:
    line: str
    decision: Decision = Decision.KEEP
    reason: str = ""
    stage: str = ""


@dataclass(frozen=True)
class ContentMask:
    lines: tuple[LineMask, ...]

    @classmethod
    def from_text(cls, text: str) -> ContentMask:
        """Split raw text on newlines and wrap each line in a KEEP LineMask."""
        split = text.split("\n")
        return cls(lines=tuple(LineMask(line=ln) for ln in split))

    def render(self) -> str:
        """Return all non-COMPRESS lines joined by newlines."""
        return "\n".join(lm.line for lm in self.lines if lm.decision is not Decision.COMPRESS)

    def stats(self) -> dict[str, int]:
        """Return a count dict for each Decision value plus total."""
        protected = sum(1 for lm in self.lines if lm.decision is Decision.PROTECT)
        compressed = sum(1 for lm in self.lines if lm.decision is Decision.COMPRESS)
        kept = len(self.lines) - protected - compressed
        return {
            "total": len(self.lines),
            "kept": kept,
            "compressed": compressed,
            "protected": protected,
        }
