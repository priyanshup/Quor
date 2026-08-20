"""deduplicate_consecutive stage: COMPRESS adjacent duplicate lines.

Tracks the last *kept* (KEEP or PROTECT) line's content. If the next KEEP
line has identical content, it is COMPRESSED. Already-COMPRESS lines are
passed through unchanged and do not update the "last kept" tracker.

`show_count` (QB-119, default False): when a run of consecutive duplicates
is found, rewrite the anchor (first) line to append a "(xN)" suffix instead
of silently dropping the repeat count — the same visible-count convention
`group_repeated` already uses for pattern-matched runs (same `_REPEAT_SUFFIX`
character), applied here to exact byte-identical consecutive duplicates
instead of same-shape ones. The anchor line is only ever rewritten when it
is itself a KEEP line the caller is free to modify — a PROTECT'd (including
preserve_pattern-matched) anchor is left completely untouched, matching
PROTECT's absolute-no-modification rule elsewhere in this pipeline; its
duplicates are still compressed exactly as before, just without a visible
count. Opt-in and additive: every existing filter that doesn't set
`show_count = true` keeps today's exact output (no dict is even consulted).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, matches_any
from quor.pipeline.stages.base import StageConfig

_REPEAT_SUFFIX = "×"  # noqa: RUF001 — same Unicode MULTIPLICATION SIGN convention as group_repeated.py


class DeduplicateConsecutiveConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    show_count: bool = False


class DeduplicateConsecutiveStage:
    """Compress consecutive duplicate KEEP lines, keeping the first occurrence."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "deduplicate_consecutive"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, DeduplicateConsecutiveConfig):
            raise TypeError(
                f"deduplicate_consecutive requires DeduplicateConsecutiveConfig, "
                f"got {type(config).__name__}"
            )

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        new_lines: list[LineMask] = []
        last_kept: str | None = None
        last_kept_idx: int | None = None
        counts: dict[int, int] = {}

        for lm in mask.lines:
            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                last_kept = lm.line
                last_kept_idx = len(new_lines) - 1
                continue

            if lm.decision is Decision.COMPRESS:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                last_kept = lm.line
                last_kept_idx = len(new_lines) - 1
                continue

            if last_kept is not None and lm.line == last_kept:
                new_lines.append(
                    LineMask(lm.line, Decision.COMPRESS, "consecutive duplicate", self.stage_type)
                )
                if last_kept_idx is not None:
                    counts[last_kept_idx] = counts.get(last_kept_idx, 1) + 1
            else:
                new_lines.append(lm)
                last_kept = lm.line
                last_kept_idx = len(new_lines) - 1

        if config.show_count:
            for idx, count in counts.items():
                anchor = new_lines[idx]
                if anchor.decision is not Decision.KEEP:
                    continue  # PROTECT anchor: never modified, count stays invisible
                new_lines[idx] = LineMask(
                    f"{anchor.line} ({_REPEAT_SUFFIX}{count})",
                    Decision.KEEP,
                    f"deduplicated {count} consecutive duplicates",
                    self.stage_type,
                )

        return ContentMask(tuple(new_lines))
