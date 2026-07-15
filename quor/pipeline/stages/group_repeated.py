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

Location-normalized mode — `location_pattern` (QB-044 slice 1, pytest only):
Some diagnostics repeat the exact same message at a different location
(file/line/nodeid) — e.g. the same assertion failing across several
parametrized pytest cases. Neither of the two modes above fits: shape
matching would risk merging genuinely different messages that happen to
share a regex shape, and exact_match would never merge them at all, since
the location makes every line byte-different. `location_pattern` is an
optional regex with exactly one capturing group spanning the location
substring to exclude from comparison; two lines are the same repetition
if they are byte-identical *after* that captured span is removed — the
message itself is never touched or re-derived. When set, the collapsed
output also differs from the `(xN)` modes: the first occurrence is kept
completely unmodified (not even a suffix appended), and one new summary
line listing the repeated locations is inserted after it — this is what
the pytest filter's inline tests require ("keep the first occurrence
exactly as-is"). Orthogonal to `exact_match`; filters that don't set
`location_pattern` (the default, `None`) are entirely unaffected.

Global scope — `scope="global"` (QB-044 slice 2, pytest only):
Real repeated failures are usually *not* adjacent — a parametrized test's
40 failures are separated by other tests' output. `scope="run"` (the
default, unchanged) only ever considers consecutive matching lines, same
as before this field existed. `scope="global"` scans the *entire* KEEP-line
stream instead: every line matching `patterns` is grouped by its
`location_pattern`-normalized key (or, if no `location_pattern` is
configured, by the raw line itself) regardless of what non-matching lines
sit between occurrences. Still zero fuzzy matching — the same
byte-identical-after-location-removal rule as run-scope's location mode,
just no longer restricted to adjacency. `exact_match` has no effect under
`scope="global"`: key-based comparison already requires exact equality
post-normalization, which is strictly stronger than shape matching.
Output shape matches run-scope's location mode exactly (first occurrence
byte-for-byte unchanged, one summary line listing the other occurrences'
locations inserted immediately after it, the rest `COMPRESS`ed) — only
*where in the document* repeats are found differs between the two scopes.
Every line that isn't part of a collapsed group — including PROTECT lines,
already-COMPRESS lines, and non-matching KEEP lines — keeps its exact
original relative position; only the grouped duplicates are removed from
their scattered positions and referenced by the one summary line. Opt-in
and additive: `scope` defaults to `"run"`, so every existing filter is
unaffected unless it explicitly sets `scope="global"`.
"""

from __future__ import annotations

import warnings
from typing import ClassVar, Literal

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
    location_pattern: str | None = Field(default=None)
    scope: Literal["run", "global"] = "run"


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
        compiled_location = _compile(config.location_pattern) if config.location_pattern else None

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
            if config.scope == "global":
                lines = _collapse_global(lines, pat, config.min_count, compiled_location, self.stage_type)
            else:
                lines = _collapse_runs(
                    lines, pat, config.min_count, config.exact_match, compiled_location, self.stage_type
                )

        return ContentMask(tuple(lines))


def _location_key(line: str, location_pattern: regex.Pattern[str]) -> tuple[str, str | None]:
    """Split `line` into (comparison_key, location_text) using `location_pattern`'s
    single capturing group. The captured span is excised from `key` — the
    message text around it is left untouched. Fails open (key=line,
    location=None) on no-match or timeout, which only ever *prevents* a
    collapse, never causes an incorrect one."""
    try:
        m = _search(location_pattern, line)
    except TimeoutError:
        warnings.warn(
            f"[quor] location_pattern {location_pattern.pattern!r} timed out; "
            "skipping location-normalized comparison for this line",
            stacklevel=3,
        )
        return line, None
    if m is None or m.lastindex is None:
        return line, None
    return line[: m.start(1)] + line[m.end(1) :], m.group(1)


def _location_summary_line(rest: list[LineMask], location_pattern: regex.Pattern[str], stage_type: str) -> LineMask:
    """Build the one summary line for a location-normalized group's
    non-first occurrences (shared by run-scope's location mode and
    global scope) — lists each repeat's location, never its message."""
    locations = [
        loc if (loc := _location_key(lm.line, location_pattern)[1]) is not None else lm.line for lm in rest
    ]
    return LineMask(
        line=f"({len(rest)} more with the same message at: {', '.join(locations)})",
        decision=Decision.KEEP,
        reason=f"grouped {len(rest)} location-normalized repetitions",
        stage=stage_type,
    )


def _collapse_runs(
    lines: list[LineMask],
    pattern: regex.Pattern[str],
    min_count: int,
    exact_match: bool,
    location_pattern: regex.Pattern[str] | None,
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
            if next_matched and location_pattern is not None:
                first_key, _ = _location_key(run[0].line, location_pattern)
                next_key, _ = _location_key(next_lm.line, location_pattern)
                if next_key != first_key:
                    # Same trigger shape, but the message (everything outside
                    # the captured location span) differs — a genuinely
                    # different failure, not a repeat at a new location.
                    next_matched = False
            if next_matched:
                run.append(next_lm)
                j += 1
            else:
                break

        count = len(run)
        if count >= min_count and location_pattern is not None:
            # Location-normalized mode (QB-044 slice 1): the first occurrence
            # is kept byte-for-byte unmodified (reused, not rebuilt), and a
            # separate summary line lists where the repeats occurred — the
            # message itself is never rewritten or re-derived.
            result.append(run[0])
            result.append(_location_summary_line(run[1:], location_pattern, stage_type))
            for repeated_lm in run[1:]:
                result.append(
                    LineMask(
                        line=repeated_lm.line,
                        decision=Decision.COMPRESS,
                        reason="grouped repetition (location-normalized)",
                        stage=stage_type,
                    )
                )
        elif count >= min_count:
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


def _collapse_global(
    lines: list[LineMask],
    pattern: regex.Pattern[str],
    min_count: int,
    location_pattern: regex.Pattern[str] | None,
    stage_type: str,
) -> list[LineMask]:
    """Collapse repeated KEEP lines matching `pattern` across the *entire*
    line stream (QB-044 slice 2), not just consecutive runs. Two lines are
    the same repetition only if byte-identical after `location_pattern`'s
    captured span is removed (or, with no `location_pattern` configured,
    byte-identical outright) — no shape matching, no fuzzy comparison.
    PROTECT lines, already-COMPRESS lines, and non-matching KEEP lines are
    never grouping candidates and always keep their exact original relative
    position; only a qualifying group's non-first occurrences move (into
    one summary line inserted right after the first occurrence).
    """
    # Pass 1: index every eligible (KEEP, pattern-matching) line by its
    # location-normalized key. Non-eligible lines are simply never visited
    # here — no state is set for them, so they pass through pass 2 as-is.
    groups: dict[str, list[int]] = {}

    for idx, lm in enumerate(lines):
        if lm.decision is not Decision.KEEP:
            continue
        try:
            if not _search(pattern, lm.line):
                continue
        except TimeoutError:
            warnings.warn(
                f"[quor] Pattern {pattern.pattern!r} timed out; skipping line for global group_repeated",
                stacklevel=3,
            )
            continue

        key = _location_key(lm.line, location_pattern)[0] if location_pattern is not None else lm.line
        groups.setdefault(key, []).append(idx)

    # Pass 2: only groups meeting min_count actually collapse. first_of[idx]
    # holds the rest-of-group indices to summarize right after `idx`;
    # compress_at holds every non-first index whose line becomes COMPRESS.
    first_of: dict[int, list[int]] = {}
    compress_at: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < min_count:
            continue
        first, *rest = idxs
        first_of[first] = rest
        compress_at.update(rest)

    # Pass 3: rebuild the line list in original order — every surviving
    # line (first occurrences, non-matching lines, PROTECT lines, etc.)
    # keeps its exact original relative position; only grouped repeats are
    # replaced with COMPRESS at their own original position, and one summary
    # line is spliced in immediately after each group's first occurrence.
    result: list[LineMask] = []
    for idx, lm in enumerate(lines):
        if idx in compress_at:
            result.append(
                LineMask(
                    line=lm.line,
                    decision=Decision.COMPRESS,
                    reason="grouped repetition (global, location-normalized)",
                    stage=stage_type,
                )
            )
            continue

        result.append(lm)

        group_rest = first_of.get(idx)
        if group_rest is not None:
            rest_lines = [lines[i] for i in group_rest]
            if location_pattern is not None:
                result.append(_location_summary_line(rest_lines, location_pattern, stage_type))
            else:
                result.append(
                    LineMask(
                        line=f"({len(group_rest)} more with the same message at: "
                        f"{', '.join(lines[i].line for i in group_rest)})",
                        decision=Decision.KEEP,
                        reason=f"grouped {len(group_rest)} repetitions (global)",
                        stage=stage_type,
                    )
                )

    return result
