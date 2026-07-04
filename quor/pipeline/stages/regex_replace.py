"""regex_replace stage: apply configurable regex substitutions to KEEP lines.

Used to normalize high-entropy content (UUIDs, timestamps, hashes, file
paths) so that other stages (deduplicate_consecutive, group_repeated) can
collapse lines that would otherwise differ only by that noise.

PROTECT lines are never modified — same invariant as every other stage;
preserve_patterns matches take precedence over substitution. Already-COMPRESS
lines are left untouched. Rules are applied in the order declared, each as a
`pattern.sub(replacement, line)` call using the `regex` package (ADR-015)
with the shared per-match timeout. Replacement strings may use backreferences
(`\\1`, `\\g<name>`) per `regex`'s own substitution syntax — no extra handling
is needed here, that support comes from using `.sub()` directly.
"""

from __future__ import annotations

import warnings
from typing import ClassVar

import regex
from pydantic import BaseModel, ConfigDict, Field

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, _sub, matches_any
from quor.pipeline.stages.base import StageConfig


class RegexReplaceRule(BaseModel):
    """One pattern -> replacement substitution rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: str
    replacement: str


class RegexReplaceConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: list[RegexReplaceRule] = Field(default_factory=list)


class RegexReplaceStage:
    """Apply one or more regex substitutions to KEEP line content, in order."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "regex_replace"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, RegexReplaceConfig):
            raise TypeError(
                f"regex_replace requires RegexReplaceConfig, got {type(config).__name__}"
            )

        if not config.rules:
            return mask

        compiled_preserve = [_compile(p) for p in config.preserve_patterns]
        compiled_rules = [(_compile(r.pattern), r.replacement) for r in config.rules]

        new_lines: list[LineMask] = []
        for lm in mask.lines:
            if lm.decision is Decision.PROTECT:
                new_lines.append(lm)
                continue

            if lm.decision is Decision.COMPRESS:
                new_lines.append(lm)
                continue

            if compiled_preserve and matches_any(lm.line, compiled_preserve):
                new_lines.append(
                    LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", self.stage_type)
                )
                continue

            new_lines.append(_apply_rules(lm, compiled_rules, self.stage_type))

        return ContentMask(tuple(new_lines))


def _apply_rules(
    lm: LineMask,
    rules: list[tuple[regex.Pattern[str], str]],
    stage_type: str,
) -> LineMask:
    """Run every rule's substitution over lm.line in order; fail-open per rule."""
    line = lm.line
    changed = False

    for pat, repl in rules:
        try:
            new_line = _sub(pat, repl, line)
        except TimeoutError:
            warnings.warn(
                f"[quor] Pattern {pat.pattern!r} timed out during regex_replace; skipping rule",
                stacklevel=3,
            )
            continue

        if new_line != line:
            changed = True
        line = new_line

    if not changed:
        return lm
    return LineMask(line, lm.decision, "regex_replace applied", stage_type)
