"""column_padding_compression stage: collapse multi-space column-alignment
padding in machine-generated tabular KEEP lines to a single space.

QB-101: fixed-width tabular tool output (`docker ps`, `kubectl get`, `ps aux`,
`df -h`, `pip list`, `poetry show`, `git branch -vv`) right-pads every column
with spaces so values line up visually in a terminal:

    NAME          READY   STATUS             RESTARTS   AGE
    frontend      1/1     Running            0          2d
    backend       1/1     Running            1          2d

That alignment is pure visual whitespace — an LLM doesn't need columns to
line up, only the field values and their left-to-right order, both of which
survive untouched when padding collapses to one separator space:

    NAME READY STATUS RESTARTS AGE
    frontend 1/1 Running 0 2d
    backend 1/1 Running 1 2d

Architecturally this is a sibling of `truncate_lines`, not of
`path_prefix_fold`/`numeric_range_compression`/`relative_timestamp_
compression`: those three all require detecting a *run* of consecutive
matching lines before they can act (a shared prefix, a consecutive integer
sequence, a previous timestamp to diff against). Collapsing padding inside
one line depends on nothing about its neighbors, so this stage — like
`truncate_lines` — processes each qualifying KEEP line independently, with no
run-scanning loop at all.

`patterns` (required, like `path_prefix_fold`/`strip_lines`/`group_repeated`):
the filter author declares which KEEP lines are table rows. Unlike
`numeric_range_compression`/`relative_timestamp_compression` (which need no
`patterns` because "the whole line is only digits"/"starts with one of 7
exact timestamp shapes" are precise, zero-false-positive structural checks),
"this line looks like a table row" has no such precise test — a prose
sentence with an accidental double space is indistinguishable from a table
row by shape alone. That ambiguity is exactly what this stage's own "no
heuristics" requirement rules out, so — same as `path_prefix_fold` — the
judgment call belongs to the filter author, who knows what command produced
the output, not to internal sniffing. A direct consequence: this stage is
never wired into `z_generic.toml` (the true fallback filter, which by
definition doesn't know what command produced its input, so it cannot
honestly declare "these are table rows" without reintroducing the
heuristic this stage is built to avoid).

Algorithm: a single hardcoded pattern (`_INTERNAL_SPACE_RUN`, stdlib `re`,
same "not user-supplied, so no `regex`-timeout needed" convention `remove_
ansi`/`numeric_range_compression` already use for their own fixed patterns)
matches a run of 2+ literal space characters with a non-whitespace character
immediately on both sides, and replaces it with one space. The lookarounds
do all of the safety work:

  - A run at the very start or end of a line never has a non-whitespace
    character on the missing side, so leading indentation and trailing
    whitespace are structurally unreachable — not a special case, just a
    consequence of the pattern requiring `\S` on both flanks.
  - A tab is whitespace, not `\S`, so a run adjacent to a tab never matches
    (and a tab in the middle of what looks like one long run of blank space
    splits it into two separate space-only runs, each independently
    re-checked against the same `\S`-flank rule) — tabs are never touched
    and never crossed.
  - Every non-whitespace token's exact spelling, and the left-to-right order
    of every token on the line, is untouched — only the *width* of the gap
    between tokens changes, never which tokens are there.

Safety against ASCII art / box-drawing / markdown tables / aligned code
comments is *not* the algorithm's job — those are all sequences of
non-whitespace glyphs the pattern above would happily collapse padding
around if a filter told it to. The real safety boundary is upstream, at the
opt-in point: no shipped filter wires this stage into content shaped like
that (see backlog.md's QB-101 entry for the investigated candidates,
including why `npm ls`/`cargo tree` — tree output, not columns — were
rejected as targets).

Losslessness: this is a QB-096-style exception, not a `path_prefix_fold`-
style one — every token and its order survive exactly, but the *exact
original padding width* is not reconstructible (you can't recover whether a
gap was originally 2, 3, or 5 spaces from the collapsed single space). This
matches the project's "maximum practical token reduction" Vision directly:
preserve enough for the AI to keep working correctly, not enough to
reconstruct byte-for-byte.

Cost gate (QB-055, same principle every sibling folding stage uses): a
qualifying line is only rewritten if the collapsed form is estimated
strictly cheaper (`line_tokens`, `ceil(len/4)`) than the original. This is
not vacuous — collapsing a single 2-space run can reduce character count
without crossing a `ceil(len/4)` token-estimate boundary (e.g. `"a  b"` (4
chars, 1 token) -> `"a b"` (3 chars, still 1 token)), so a real fraction of
qualifying lines are correctly left untouched by the gate. No mask.py
exception is required for this stage at all: it rewrites a qualifying KEEP
line's content in place, never assigns COMPRESS, never inserts a line, and
never changes the mask's total line count — exactly `truncate_lines`'s
existing shape, not a new one (see that stage's own module docstring and
`quor/pipeline/mask.py`'s module docstring for both stages named together).

`max_gaps` (optional, default unbounded — a real bug found during
implementation, not a speculative safeguard): a table's *trailing* free-text
column can legitimately contain multiple consecutive spaces as real content
— an `ls -l` filename, a `ps aux` COMMAND, a `git branch -vv` commit
subject. An unbounded collapse cannot tell "padding between columns" apart
from "a double space the user actually typed" in that column, and silently
rewriting the latter is a correctness bug, not a compression trade-off —
it violates this stage's own "meaning must be identical" bar. Every
metadata-only table (`docker images`, `kubectl get pods`, `pip list` — no
column can contain internal whitespace by the underlying tool's own naming
rules) is safe with the default unbounded behavior. Any table with a
free-text tail column must set `max_gaps` to the exact number of column
separators *before* that tail column; `_INTERNAL_SPACE_RUN.sub()` is called
with `count=max_gaps` (stdlib `re`'s own left-to-right "first N matches
only" semantics — `count=0` means unlimited, `re`'s existing convention,
which is exactly what "no limit configured" should mean), so every
character from the (N+1)th qualifying gap onward — including any further
internal spacing — is copied through completely untouched, not just
"probably fine."
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import (
    _compile,
    apply_preserve_patterns,
    line_tokens,
    matches_any,
)
from quor.pipeline.stages.base import StageConfig

# A run of 2+ literal spaces with a non-whitespace character immediately
# before and after it. `\S` excludes tabs (and all other whitespace), so a
# run touching a tab on either side never matches; a run at line start/end
# never has a flanking `\S` on the missing side, so it never matches either.
_INTERNAL_SPACE_RUN = re.compile(r"(?<=\S) {2,}(?=\S)")


class ColumnPaddingCompressionConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: list[str] = Field(default_factory=list)
    max_gaps: int | None = Field(default=None, ge=1)


class ColumnPaddingCompressionStage:
    """Collapse internal alignment-space runs in filter-declared table-row KEEP lines."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "column_padding_compression"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, ColumnPaddingCompressionConfig):
            raise TypeError(
                "column_padding_compression requires ColumnPaddingCompressionConfig, "
                f"got {type(config).__name__}"
            )

        if not config.patterns:
            return mask

        compiled_patterns = [_compile(p) for p in config.patterns]
        lines = apply_preserve_patterns(list(mask.lines), config.preserve_patterns, self.stage_type)
        # re's own convention: count=0 means "replace all" — exactly what
        # "no max_gaps configured" (unbounded) should mean.
        sub_count = config.max_gaps if config.max_gaps is not None else 0

        result: list[LineMask] = []
        for lm in lines:
            if lm.decision is not Decision.KEEP or not matches_any(lm.line, compiled_patterns):
                result.append(lm)
                continue

            collapsed = _INTERNAL_SPACE_RUN.sub(" ", lm.line, count=sub_count)
            if collapsed == lm.line:
                result.append(lm)
                continue

            if line_tokens(collapsed) >= line_tokens(lm.line):
                result.append(lm)
                continue

            result.append(
                LineMask(
                    collapsed,
                    Decision.KEEP,
                    "collapsed column-alignment padding",
                    self.stage_type,
                )
            )

        return ContentMask(tuple(result))
