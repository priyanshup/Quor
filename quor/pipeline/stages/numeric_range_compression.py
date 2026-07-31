"""numeric_range_compression stage: collapse a run of consecutive standalone
integer lines into an inclusive `start-end` range.

QB-097: tool output that lists integers one per line — line numbers, test
IDs, coverage/warning/issue IDs, stack-frame indices, log sequence numbers —
is common and highly redundant when the values are consecutive:

    101
    102
    103
    104
    105

becomes:

    101-105

Same idea as `path_prefix_fold` (QB-095), applied to consecutive integers
instead of a shared path prefix.

Scope: standalone numeric lines only, not "Line 101"/"Line 102". A line only
qualifies if, after matching `^\\d+$`, *nothing else* is on it. Detecting "a
numeric line" this way is a precise structural fact, not a heuristic guess —
unlike `path_prefix_fold`'s path-like shape (which is genuinely ambiguous,
hence filter-declared `patterns`), an entire line being nothing but digits
has no false-positive risk, so this stage needs no `patterns` config at all;
a filter opts in simply by including this stage in its TOML. Merging a
constant text prefix with a varying numeric suffix (`Line 101` → `Line
101-103`) is a different, harder problem — it re-implements the shared-
prefix logic `path_prefix_fold` already owns, on a *different* boundary
(trailing, not `separator`-delimited) — and is deliberately left out of
this stage's scope; a future stage could tackle it directly if warranted.

Negative numbers are never merged — recognized (matching `^\\d+$` requires no
leading `-`, so a negative line is simply never a run candidate) but always
left as an isolated KEEP line, on purpose: `-` is this stage's own range
separator, so `-5` through `-1` rendered as `-5--1` would be genuinely
ambiguous to a reader parsing the line left to right (`-5`, `-`, `-1`? or
`-5-`, `-1`?) — exactly the kind of ambiguity "remain human-readable" and
"never invent data" rule out. This is an explicit rejection, not an
oversight: negative integer lines are always left untouched, never grouped
with each other, and this is covered by a dedicated test.

Consecutiveness is `next == previous + 1` only — strictly ascending, no
duplicates, no descending runs merge (each of those breaks the run instead;
`5, 4, 3` and `5, 5, 6` both stay as isolated lines). Rendering a range
always implies ascending order, so this is the only rule consistent with
"never reorder data" and "never merge non-consecutive values".

Leading zeros: a run only merges lines of *identical string width* (the
whole run, not just consecutive pairs, must share `len(line)` with the run's
first entry). This one rule handles zero-padding fidelity for free: `"001"`,
`"002"`, `"003"` (width 3) fold to `"001-003"`, and expanding that range by
zero-padding each integer to width 3 reproduces every original line
byte-for-byte. A width change always breaks the run instead of attempting to
reformat a line to a width it wasn't already written in (`"99"` next to
`"100"` never merges) — this is deliberately conservative: it also declines
some safe, unpadded, width-crossing runs (`"9"`, `"10"`, `"11"`) that could
in principle merge without any fidelity risk, traded for one uniform,
easy-to-verify invariant rather than two separate padded/unpadded code
paths. Flagged in backlog.md as a possible follow-up, not attempted here.

Folding a run is a token-cost decision (QB-055 principle, same as
`collapse_unchanged_context`/`path_prefix_fold`), not a line-count
threshold: strictly fewer estimated tokens than leaving the run as-is, or it
is left untouched. Note this makes some of this ticket's own worked
examples involving two-item runs of equal-width numbers unreachable in
practice: joining two equal-width lines with `-` (`"12-13"`) is always
*exactly* as many characters as the original two lines joined by `\n`
(`"12\\n13"`) — `-` and `\\n` are both one character — so a same-width
2-line run can never be *strictly* cheaper under any length-based token
estimate, only tie. Given the ticket's own repeated, explicit "never
compress if the savings are zero" requirement, this stage honors the gate
over those illustrative examples; see `TestNumericRangeCompression`'s
`test_two_digit_pair_ties_and_is_left_unfolded` for the concrete case this
produces (documented in backlog.md's QB-097 entry).

Rendering reuses `group_repeated`'s/`collapse_unchanged_context`'s existing
mask.py exception (rewrite the run's first line to a summary, `COMPRESS` the
rest) rather than `path_prefix_fold`'s "insert a new header line" one — a
range fully replaces the run with no children to keep, so there is nothing
to insert. This means the total `ContentMask` line count is unchanged, and
no new mask.py exception category is needed.
"""

from __future__ import annotations

import math
import re
from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig

_INTEGER_LINE = re.compile(r"^\d+$")


class NumericRangeCompressionConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NumericRangeCompressionStage:
    """Fold a run of consecutive standalone integer KEEP lines into a range."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "numeric_range_compression"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, NumericRangeCompressionConfig):
            raise TypeError(
                "numeric_range_compression requires NumericRangeCompressionConfig, "
                f"got {type(config).__name__}"
            )

        lines = list(mask.lines)

        if config.preserve_patterns:
            compiled_preserve = [_compile(p) for p in config.preserve_patterns]
            lines = [
                LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                if lm.decision is not Decision.PROTECT and matches_any(lm.line, compiled_preserve)
                else lm
                for lm in lines
            ]

        result: list[LineMask] = []
        i = 0
        n = len(lines)

        while i < n:
            lm = lines[i]
            if lm.decision is not Decision.KEEP or not _INTEGER_LINE.fullmatch(lm.line):
                result.append(lm)
                i += 1
                continue

            run = [lm]
            width = len(lm.line)
            value = int(lm.line)
            j = i + 1

            while j < n:
                nxt = lines[j]
                if nxt.decision is not Decision.KEEP or not _INTEGER_LINE.fullmatch(nxt.line):
                    break
                if len(nxt.line) != width:
                    break
                nxt_value = int(nxt.line)
                if nxt_value != value + 1:
                    break
                run.append(nxt)
                value = nxt_value
                j += 1

            result.extend(_fold_run(run, self.stage_type))
            i = j

        return ContentMask(tuple(result))


def _line_tokens(line: str) -> int:
    """Estimate a line's token cost: ceil(len(line) / 4), same as
    `path_prefix_fold`/`collapse_unchanged_context`/`max_tokens`."""
    return max(1, math.ceil(len(line) / 4))


def _fold_run(run: list[LineMask], stage_type: str) -> list[LineMask]:
    """Fold `run` (2+ consecutive, same-width, ascending-by-1 integer KEEP
    LineMasks) to one `start-end` line, if doing so is estimated to cost
    strictly fewer tokens than leaving it as-is."""
    if len(run) < 2:
        return run

    compressed_text = f"{run[0].line}-{run[-1].line}"
    original_cost = sum(_line_tokens(lm.line) for lm in run)
    compressed_cost = _line_tokens(compressed_text)
    if compressed_cost >= original_cost:
        return run

    head = LineMask(
        line=compressed_text,
        decision=Decision.KEEP,
        reason=f"merged {len(run)} consecutive integers into range {compressed_text}",
        stage=stage_type,
    )
    tail = [
        LineMask(lm.line, Decision.COMPRESS, "merged into numeric range", stage_type)
        for lm in run[1:]
    ]
    return [head, *tail]
