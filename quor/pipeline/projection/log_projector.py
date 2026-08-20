"""log_projector: QB-119 CLI log/output projection for tool payloads.

Deliberately NOT a new regex engine: `quor/pipeline/stages/remove_ansi.py`
already strips ANSI-only lines and (via its opt-in `strip_inline` field,
QB-119) codes embedded inline in real content; `quor/pipeline/stages/
deduplicate_consecutive.py` already collapses consecutive duplicate lines
and (via its opt-in `show_count` field, QB-119) can annotate the collapsed
count instead of dropping it silently. Both are also wired into the
built-in `generic` filter (`quor/filters/builtin/z_generic.toml`) with
those fields turned on, so any command/tool output that falls through to
`generic` already gets this treatment via the normal ContentMask pipeline.

This module exists as a small, directly callable/testable composition of
those same two stages for a caller that has raw log text in hand and wants
projection applied without going through filter lookup — mirroring why
`json_projector.py` is a standalone pre-pass rather than a stage. Building
a second, parallel ANSI-stripping/dedup implementation here would only
create two places the same bug could be fixed in one and not the other.

Verbatim preservation of stack traces/error messages/assertions is not a
separate rule bolted on here: neither stage ever touches a line that isn't
either pure ANSI, has ANSI embedded alongside its text, or is a byte-exact
duplicate of the immediately preceding kept line — a real, specific error
message essentially never satisfies any of those, so it always survives
untouched by construction, not by pattern-matching for "looks like an
error."

Fail-open: any unexpected failure returns the original text unchanged.
"""

from __future__ import annotations

from quor.pipeline.mask import ContentMask
from quor.pipeline.stages.deduplicate_consecutive import (
    DeduplicateConsecutiveConfig,
    DeduplicateConsecutiveStage,
)
from quor.pipeline.stages.remove_ansi import RemoveAnsiConfig, RemoveAnsiStage

_REMOVE_ANSI = RemoveAnsiStage()
_REMOVE_ANSI_CONFIG = RemoveAnsiConfig(type="remove_ansi", strip_inline=True)
_DEDUPLICATE = DeduplicateConsecutiveStage()
_DEDUPLICATE_CONFIG = DeduplicateConsecutiveConfig(type="deduplicate_consecutive", show_count=True)


def project_log_text(text: str) -> str:
    """Strip ANSI codes and collapse consecutive duplicate lines to a
    visible count. See module docstring for why this composes existing
    stages rather than reimplementing their logic."""
    try:
        mask = ContentMask.from_text(text)
        mask = _REMOVE_ANSI.apply(mask, _REMOVE_ANSI_CONFIG)
        mask = _DEDUPLICATE.apply(mask, _DEDUPLICATE_CONFIG)
        return mask.render()
    except Exception:  # noqa: BLE001 — fail-open: malformed/unexpected input must never raise
        return text
