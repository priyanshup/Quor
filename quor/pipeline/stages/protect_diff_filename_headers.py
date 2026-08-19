"""protect_diff_filename_headers stage: never let a diff segment lose its
only filename-carrying line.

Found during a QB-041 audit. `git-diff`'s `strip_lines` stage unconditionally
strips every `diff --git a/X b/Y` header line, relying on `--- a/X`/
`+++ b/Y` to carry the filename forward for the AI to read (see git.toml's
own comment on that load-bearing collision). That assumption holds for an
ordinary content change, a binary file, and a rename — each of those leaves
behind a `--- a/`/`+++ b/`/`Binary files `/`rename from `/`rename to ` line
that `strip_lines`'s own `preserve_patterns` already protects.

It does not hold for three real, verified `git diff` shapes, each of which
emits *only* the `diff --git ` header and a boilerplate mode/index line, with
no other line anywhere in the segment naming the file:
  - a mode-only change (`old mode 100644` / `new mode 100755`, chmod)
  - a newly added file with zero content (no `--- /dev/null`/`+++ b/X` pair
    is ever emitted for an empty file — git has no hunk to show)
  - a deleted file that was already empty (same reasoning, in reverse)

Confirmed directly against Quor's own filter pipeline before this stage
existed: all three inputs above compressed to a line mentioning only "old
mode"/"new file mode"/"deleted file mode" — the filename was gone entirely,
not just less verbose. That is a meaning-loss bug (ANTI_GOALS.md #3), not an
over-compression judgment call.

This stage runs first in `git-diff`'s stage chain, before `strip_lines` ever
sees the input, and marks a `diff --git ` header line PROTECT only when
scanning the rest of its own file segment finds none of the five patterns
that already carry a filename elsewhere. `strip_lines` already honors an
existing PROTECT decision unconditionally (see that stage's own docstring),
so no change to `strip_lines` itself is needed. Ordinary diffs are
byte-identical to before this stage existed: every segment with real content,
a rename, or a binary marker already has a filename carrier, so this stage
never touches their `diff --git ` line.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages.base import StageConfig

_DIFF_GIT_HEADER = re.compile(r"^diff --git ")
_FILENAME_CARRIER_PATTERNS = (
    re.compile(r"^--- a/"),
    re.compile(r"^\+\+\+ b/"),
    re.compile(r"^rename from "),
    re.compile(r"^rename to "),
    re.compile(r"^Binary files "),
)


class ProtectDiffFilenameHeadersConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProtectDiffFilenameHeadersStage:
    """PROTECT a `diff --git ` header line when it is the only line in its
    own file segment that names the changed file."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "protect_diff_filename_headers"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, ProtectDiffFilenameHeadersConfig):
            raise TypeError(
                f"protect_diff_filename_headers requires "
                f"ProtectDiffFilenameHeadersConfig, got {type(config).__name__}"
            )

        lines = list(mask.lines)
        header_indices = [
            i for i, lm in enumerate(lines) if _DIFF_GIT_HEADER.match(lm.line)
        ]
        if not header_indices:
            return mask

        segment_bounds = [*header_indices[1:], len(lines)]
        protect_indices: set[int] = set()
        for start, end in zip(header_indices, segment_bounds, strict=True):
            segment = lines[start + 1 : end]
            has_carrier = any(
                pattern.match(lm.line)
                for lm in segment
                for pattern in _FILENAME_CARRIER_PATTERNS
            )
            if not has_carrier:
                protect_indices.add(start)

        if not protect_indices:
            return mask

        result = [
            (
                LineMask(
                    lm.line,
                    Decision.PROTECT,
                    "sole filename carrier in this diff segment",
                    self.stage_type,
                )
                if i in protect_indices and lm.decision is Decision.KEEP
                else lm
            )
            for i, lm in enumerate(lines)
        ]
        return ContentMask(tuple(result))
