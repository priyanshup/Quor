"""group_repeated stage: collapse N+ consecutive matching KEEP lines to first + count.

Example: 5 consecutive "WARNING: disk low" lines with pattern "^WARNING:"
and min_count=2 becomes:
  "WARNING: disk low (x5)"   <- KEEP, content updated
  "WARNING: disk low"        <- COMPRESS  (x4)

The multiplication character used in the suffix is the Unicode MULTIPLICATION
SIGN (U+00D7) so the AI sees: "WARNING: disk low (x5)" -- visually clear.

PROTECT lines break a run; they are never modified or compressed.
Already-COMPRESS lines are also treated as run-breakers.

Per-pattern processing: for each pattern in config.patterns, a separate
collapse pass is run. Patterns are matched with timeout via _search.

Matching mode — `exact_match` (default False, QB-006B):
By default a run only requires every line to match the same *pattern*
(shape), not to be the same text — this is deliberate and several existing
filters depend on it: mypy's build.toml config collapses the same error
*message* recurring at different line numbers in the same file (e.g. the
same "incompatible type" error on lines 12, 34, and 58), which are
different strings but the same shape. Changing this default would silently
break that filter's tested behavior.

Some filters need the stricter guarantee — ESLint's node.toml config wants
to collapse only byte-identical repeated diagnostics, never merge two
different rule violations just because they share the "L:C  error  ..."
shape. `exact_match=True` opt-in adds one extra condition to run
continuation: the candidate line must equal the run's first line exactly,
in addition to matching the pattern. This is additive and per-stage-config
— every existing filter that doesn't set it keeps its current behavior
unchanged.
"""

from __future__ import annotations

import warnings
from typing import ClassVar

import regex
from pydantic import ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, _search, matches_any
from quor.pipeline.stages.base import StageConfig

_REPEAT_SUFFIX = "×"  # noqa: RUF001 — Unicode MULTIPLICATION SIGN used in collapsed-run suffix


class GroupRepeatedConfig(StageConfig):
    """
    exact_match=False (default) — group by shape: lines matching the same
    `patterns` entry collapse together even if their text differs. Use this
    when the specific value doesn't matter, only that "N similar things
    happened" — e.g. mypy's build.toml config collapsing the same error
    message at different line numbers, or npm/npx/pnpm/yarn's build.toml/
    node.toml configs collapsing deprecation/peer-dependency warnings
    regardless of which package triggered each one.

    exact_match=True — group only lines that are byte-identical to the run's
    first line. Use this when two lines sharing the same shape are still
    genuinely distinct and must never be merged — e.g. eslint's node.toml
    config, where "L:C  error  ..." matches every violation but different
    rule names/messages/locations are different diagnostics, not repeats.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: list[str] = Field(default_factory=list)
    min_count: int = 2
    exact_match: bool = False


class GroupRepeatedStage:
    """Collapse consecutive runs of matching KEEP lines into a single summary line."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "group_repeated"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, GroupRepeatedConfig):
            raise TypeError(
                f"group_repeated requires GroupRepeatedConfig, got {type(config).__name__}"
            )

        if not config.patterns:
            return mask

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        compiled_patterns = [_compile(p) for p in config.patterns]

        lines = list(mask.lines)

        # Apply preserve_patterns first — this sets PROTECT before run detection
        if compiled_preserve:
            lines = [
                LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                if lm.decision is not Decision.PROTECT and matches_any(lm.line, compiled_preserve)
                else lm
                for lm in lines
            ]

        # For each pattern, run a collapse pass over the current line list
        for pat in compiled_patterns:
            lines = _collapse_runs(lines, pat, config.min_count, config.exact_match, self.stage_type)

        return ContentMask(tuple(lines))


def _collapse_runs(
    lines: list[LineMask],
    pattern: regex.Pattern[str],
    min_count: int,
    exact_match: bool,
    stage_type: str,
) -> list[LineMask]:
    """Collapse consecutive KEEP lines matching `pattern` into a summary + COMPRESS."""
    result: list[LineMask] = []
    i = 0

    while i < len(lines):
        lm = lines[i]

        # PROTECT and COMPRESS lines break any run
        if lm.decision is not Decision.KEEP:
            result.append(lm)
            i += 1
            continue

        # Check if this KEEP line matches the pattern
        matched = False
        try:
            if _search(pattern, lm.line):
                matched = True
        except TimeoutError:
            warnings.warn(
                f"[quor] Pattern {pattern.pattern!r} timed out; skipping group_repeated for this line",
                stacklevel=3,
            )

        if not matched:
            result.append(lm)
            i += 1
            continue

        # Found the start of a potential run — collect all consecutive matching KEEP lines
        run: list[LineMask] = [lm]
        j = i + 1

        while j < len(lines) and lines[j].decision is Decision.KEEP:
            next_lm = lines[j]
            next_matched = False
            try:
                if _search(pattern, next_lm.line):
                    next_matched = True
            except TimeoutError:
                warnings.warn(
                    f"[quor] Pattern {pattern.pattern!r} timed out; ending run",
                    stacklevel=3,
                )
            if next_matched and exact_match and next_lm.line != run[0].line:
                # Same shape, different text — with exact_match this is a
                # genuinely different diagnostic, not a repetition. Ends the
                # run here rather than merging it in.
                next_matched = False
            if next_matched:
                run.append(next_lm)
                j += 1
            else:
                break

        count = len(run)
        if count >= min_count:
            # Collapse: replace first line content, compress the rest
            first = run[0]
            result.append(
                LineMask(
                    line=f"{first.line} ({_REPEAT_SUFFIX}{count})",
                    decision=Decision.KEEP,
                    reason=f"grouped {count} repetitions",
                    stage=stage_type,
                )
            )
            for repeated_lm in run[1:]:
                result.append(
                    LineMask(
                        line=repeated_lm.line,
                        decision=Decision.COMPRESS,
                        reason="grouped repetition",
                        stage=stage_type,
                    )
                )
        else:
            # Run too short — keep as-is
            result.extend(run)

        i = j

    return result
