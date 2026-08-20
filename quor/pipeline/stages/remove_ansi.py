"""remove_ansi stage: COMPRESS lines that consist only of ANSI escape codes.

Lines that have real text content alongside ANSI codes are left unchanged;
only pure ANSI-code lines (nothing printable after stripping) are compressed.

This stage uses an internal hardcoded pattern (not user-defined) so it uses
the stdlib `re` module and has no per-line timeout overhead.

`strip_inline` (QB-119, default False): also rewrite a KEEP line's content
to remove ANSI codes embedded alongside real text (e.g. pytest's colorized
"\x1b[32mPASSED\x1b[0m tests/test_foo.py") instead of only compressing
lines that are pure ANSI. This is the same "content changes, decision and
line count don't, no run required" shape `quor/pipeline/mask.py`'s module
docstring already documents for `truncate_lines`/`column_padding_compression`
— each qualifying line is handled independently, nothing is inferred, and
every printable character survives. Opt-in and additive: every existing
filter that doesn't set `strip_inline = true` keeps today's exact behavior.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class RemoveAnsiConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strip_inline: bool = False


class RemoveAnsiStage:
    """Compress lines that are pure ANSI escape codes (no printable content)."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "remove_ansi"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, RemoveAnsiConfig):
            raise TypeError(
                f"remove_ansi requires RemoveAnsiConfig, got {type(config).__name__}"
            )

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        new_lines: list[LineMask] = []

        for lm in mask.lines:
            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                continue

            if _is_ansi_only(lm.line):
                new_lines.append(
                    LineMask(lm.line, Decision.COMPRESS, "ansi-only line", self.stage_type)
                )
            elif config.strip_inline and _ANSI_RE.search(lm.line):
                new_lines.append(
                    LineMask(
                        _ANSI_RE.sub("", lm.line),
                        Decision.KEEP,
                        "ansi codes stripped inline",
                        self.stage_type,
                    )
                )
            else:
                new_lines.append(lm)

        return ContentMask(tuple(new_lines))


def _is_ansi_only(line: str) -> bool:
    """Return True if the line contains ANSI codes but no printable content."""
    if not _ANSI_RE.search(line):
        return False
    return not _ANSI_RE.sub("", line).strip()
