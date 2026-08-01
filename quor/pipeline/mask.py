"""Core ContentMask primitive.

Every pipeline stage reads a ContentMask and returns a new ContentMask.
Stages annotate lines via Decision; the final render step applies the mask.

Invariants (enforced by Pipeline.execute, not by individual stages):
- PROTECT decisions are absolute — no subsequent stage can downgrade them.
- Line content is never modified by stages that set COMPRESS/KEEP decisions.
  group_repeated and collapse_unchanged_context are two exceptions: each may
  replace one line in a collapsed run with a summary/placeholder string
  (e.g. "msg (xN)" or "... N unchanged lines omitted ..."). numeric_range_
  compression (QB-097) is a third user of this same exception, not a new
  category: a run of consecutive same-width standalone integer lines has its
  first line rewritten to an inclusive "start-end" range, the rest COMPRESSed
  — no new LineMask is inserted, so the mask's total line count is unchanged,
  unlike path_prefix_fold below.
- path_prefix_fold (QB-095) is a third, narrower exception: it may rewrite
  every line in a matched run (not just the first) to its separator-trimmed
  suffix, and insert one new header LineMask ahead of the run announcing the
  shared prefix — the only stage besides group_repeated allowed to change
  the mask's total line count. Deliberately approved as a distinct category
  from the first two: it never discards a line's content (every original
  filename is reconstructible from header + child, byte-for-byte), it never
  introduces an alias/reference a reader has to resolve elsewhere, and the
  rewrite is pure, mechanical substring-stripping — no judgment call.
- python_ast_summarize/code_ast_summarize (QB-096) gain a fourth exception,
  narrower than path_prefix_fold's in a different way. Import-block
  collapsing may replace a whole run of consecutive, top-level import
  statements with one synthesized block whose content contains embedded
  newlines (a placeholder that itself renders as several lines, not one) —
  the run's first line is rewritten to hold it, every other line in the run
  is COMPRESSed; unlike path_prefix_fold, the mask's total line count is
  unchanged (no new LineMask is inserted — this exception reuses
  group_repeated's/collapse_unchanged_context's "rewrite the first line,
  compress the rest" shape, not path_prefix_fold's "insert a header" one).
  This does NOT meet path_prefix_fold's byte-for-byte reconstructibility
  bar either — the collapsed block re-expresses each import's module/name/
  alias, not its exact original formatting (spacing, the literal `import`
  keyword, comma placement) — so it is justified on different grounds
  instead: every individual module, name, alias, relative dot-prefix, and
  wildcard from the run survives *somewhere* in the block (nothing is
  silently dropped, hidden, or inferred away), the transform is fully
  deterministic given the language's own real parser (never a guess at what
  "looks like" an import), and it is gated by the same token-cost
  comparison as every collapsing stage above — never fires unless
  demonstrably cheaper.
- relative_timestamp_compression (QB-098) is a fifth exception, shaped
  differently from all four above: it rewrites every line in a matched run
  *except the first* (left completely untouched) to a `+delta` prefix
  relative to the immediately preceding line, and — unlike every stage
  above — never assigns COMPRESS to anything and never inserts a new
  LineMask. Every line in the run stays KEEP; the mask's total line count is
  unchanged. Reconstructibility comes from arithmetic, not byte-for-byte
  suffix preservation: the first line gives an absolute reference point, and
  each subsequent line's absolute timestamp is exactly `previous + this
  line's delta`, computed with exact integer nanosecond arithmetic (no
  floats, never rounded). Gated by the same token-cost comparison as every
  stage above. A future stage wanting a sixth exception should be held to
  one of these five bars explicitly, not treated as an open door.
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
