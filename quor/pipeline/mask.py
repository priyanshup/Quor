"""Core ContentMask primitive.

Every pipeline stage reads a ContentMask and returns a new ContentMask.
Stages annotate lines via Decision; the final render step applies the mask.

Invariants (enforced by Pipeline.execute, not by individual stages):
- PROTECT decisions are absolute — no subsequent stage can downgrade them.
- Line content is never modified by stages that set COMPRESS/KEEP decisions.
  group_repeated and collapse_unchanged_context are two exceptions: each may
  replace one line in a collapsed run with a summary/placeholder string
  (e.g. "msg (xN)" or "... N unchanged lines omitted ...").
- path_prefix_fold (QB-095) is a third, narrower exception: it may rewrite
  every line in a matched run (not just the first) to its separator-trimmed
  suffix, and insert one new header LineMask ahead of the run announcing the
  shared prefix — the only stage besides group_repeated allowed to change
  the mask's total line count. Deliberately approved as a distinct category
  from the first two: it never discards a line's content (every original
  filename is reconstructible from header + child, byte-for-byte), it never
  introduces an alias/reference a reader has to resolve elsewhere, and the
  rewrite is pure, mechanical substring-stripping — no judgment call. A
  future stage wanting a fourth exception should be held to that same bar,
  not treated as an open door.
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
