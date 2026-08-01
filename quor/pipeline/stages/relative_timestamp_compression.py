"""relative_timestamp_compression stage: rewrite a run of consecutive
timestamp-prefixed KEEP lines to per-line deltas from the previous line.

QB-098: line-oriented tool output that repeats a full timestamp on every
line — `docker logs --timestamps`, `kubectl logs --timestamps`, ISO-mode
`journalctl` output, CI console logs, application logs, a dev-server's watch
mode — spends a large, mostly-redundant share of its tokens on that
timestamp. What matters to a reader (human or LLM) is usually not the
absolute wall-clock value on every line, it's how far apart consecutive
events were:

    2026-07-31 10:15:01 INFO Starting server
    2026-07-31 10:15:02 INFO Loading plugins
    2026-07-31 10:15:03 INFO Ready
    2026-07-31 10:15:04 INFO Listening on :8080

becomes:

    2026-07-31 10:15:01 INFO Starting server
    +1s INFO Loading plugins
    +1s INFO Ready
    +1s INFO Listening on :8080

Same family as `path_prefix_fold` (QB-095) and `numeric_range_compression`
(QB-097): a deterministic, structural, filter-author-config-free fold gated
by a strict token-cost comparison. Same "no `patterns` config" reasoning as
`numeric_range_compression` too — "the line starts with one of a fixed list
of exact timestamp shapes" is a precise structural check, not a shape guess,
so there is nothing for a filter author to scope with a regex. And the same
"a false positive costs nothing" argument that justifies skipping a
`patterns` opt-in for numeric_range_compression applies here even more
directly: even if some line that merely *looks* like `HH:MM:SS` at its start
isn't semantically a clock reading, the fold is still exactly reconstructible
by arithmetic (`previous + delta = next`) — there's no scenario where folding
a false positive loses or corrupts information.

Supported formats (deterministic only — no fuzzy parsing, no locale-
dependent parsing, no natural-language dates, per QB-098's own scope):

    2026-07-31 10:15:01              space_datetime
    2026-07-31T10:15:01Z             iso_z
    2026-07-31T10:15:01.123Z         iso_frac_z
    2026-07-31T10:15:01+05:30        iso_offset
    2026-07-31T10:15:01.123+05:30    iso_frac_offset
    10:15:01                         time_only
    10:15:01.456                     time_frac

Deliberately excluded, on purpose, not by oversight:

  - journalctl's *default* syslog-style prefix (`Jul 31 10:15:01`) — a
    month name is a locale-dependent, natural-language token, exactly what
    the ticket rules out. Only ISO-mode journalctl output (`journalctl
    -o short-iso` / `-o short-iso-precise` / `--utc` with an ISO-8601
    timestamp) matches `space_datetime`/`iso_offset`/`iso_frac_offset`
    above. This is a real, intentional coverage gap, not a bug — see
    backlog.md's QB-098 entry.
  - Bracket-wrapped or otherwise-prefixed timestamps (`[2026-07-31T10:15:01Z]
    message`, `pod/container 2026-07-31T10:15:01Z message`) — every format
    above is anchored at column 0 (`^`) and must be followed immediately by
    whitespace or end-of-line. A leading `[` or another token before the
    timestamp is simply a non-match, same fail-open behavior as any other
    unrecognized line shape.
  - Unix epoch integers (`1785499701`). A bare digit run is
    `numeric_range_compression`'s territory, not this stage's — the two
    stages never compete for the same line because this stage only matches
    an `HH:MM:SS`-shaped (or fuller) prefix, never a lone integer.
  - Mixed formats, or a change in fractional-digit width, within one run —
    see "Run-breaking rules" below.

Timezone handling: an explicit `[+-]HH:MM`/`[+-]HHMM` offset is converted to
an absolute UTC instant before computing a delta, so an `iso_offset`/
`iso_frac_offset` run may freely mix *different offset values* line to line
(e.g. a log spanning a DST transition) — the underlying instant is always
what's compared, never the raw local clock reading. This does NOT mean a run
may mix `Z` with a numeric offset, or a numeric offset with no timezone at
all: `iso_z`/`iso_offset`/`space_datetime` are three distinct format kinds,
and rule 2 below (same kind for the whole run) still applies — in practice a
single log stream never switches its own timestamp *format* mid-stream even
if the underlying offset value legitimately changes, so this costs nothing
in coverage. A timestamp with no timezone at all (`space_datetime`,
`time_only`, `time_frac`) is treated as a naive clock reading with no
conversion; this is correct as long as the whole run shares one implicit
timezone, which is true of every real-world source this stage targets (a
single process/container emitting its own log stream doesn't change
timezone mid-stream).

Run-breaking rules (mirrors `numeric_range_compression`'s "identical width"
precedent, applied to timestamp shape instead of digit width): a run only
continues while every line

  1. is a KEEP line (PROTECT/COMPRESS breaks adjacency, same as every
     sibling stage);
  2. matches the exact same format kind as the run's first line;
  3. has the same fractional-digit count as the run's first line, if the
     format kind has a fractional part (deliberately conservative, same
     "one uniform, easy-to-verify invariant" trade `numeric_range_
     compression` documents for its own width rule — arithmetic doesn't
     strictly require this since fractional digits are exact-padded to
     nanoseconds either way, but a run mixing "10:15:01.1" and
     "10:15:01.12" is disallowed on purpose to keep the invariant simple);
  4. parses to a value *no earlier* than the previous line's value (a
     decrease breaks the run — could be legitimate clock skew, a log
     rotation splice, or day rollover on a `time_only`/`time_frac` line with
     no date to disambiguate; "when uncertain, don't collapse" wins, same
     rule `numeric_range_compression` applies to descending/duplicate runs).

A run of length 1 never folds (nothing to compute a delta against).

Delta rendering and exactness: every timestamp is converted to an integer
count of nanoseconds (whole-second component plus its fractional part
zero-padded out to 9 digits — padding with zeros changes only the
*resolution* of the representation, never the value, so this is exact, not
an approximation) before subtracting. `+Nh`/`+Nm`/`+Ns`/`+Nms`/`+Nus`/`+Nns`
is rendered using the *largest* unit that divides the delta evenly — nanoseconds
always divides everything, so this always terminates and never rounds.
"Never round, never approximate" (QB-098's own requirement) is satisfied by
construction: no floating-point arithmetic is used anywhere in this module.

Rendering shape and the new mask.py exception: this stage rewrites every
line in a folded run *except the first* (which is left completely
untouched — same object, same content, same decision) — each subsequent
line becomes `f"+{delta}{rest_of_line}"`, where `rest_of_line` is the
original line with only its matched timestamp token stripped (the
whitespace that followed the timestamp is part of `rest_of_line` and
survives unchanged, so "+1s INFO Ready" keeps the same single space
"10:15:03 INFO Ready" had). No line is ever COMPRESSed and no line is ever
inserted — every line in the run stays KEEP and the mask's total line count
is unchanged. This is a fifth, narrower exception to mask.py's "line content
is never modified" rule, distinct from `numeric_range_compression`'s
rewrite-first-compress-rest shape and from `path_prefix_fold`'s
insert-a-header shape: here it is every line *but* the first that gets
rewritten, and nothing is ever hidden behind COMPRESS. Reconstructibility:
line 1 gives the absolute start; each subsequent line's absolute timestamp
is exactly `previous absolute value + this line's delta` (addition only,
by inspection, no lookup elsewhere required) — same "independently
understandable" bar every other stage in this pipeline meets.

Folding a run is a token-cost decision (same QB-055 principle every sibling
folding stage uses): a run is only rewritten if doing so is estimated to
cost strictly fewer tokens than leaving it untouched; otherwise every line
in the run is left completely as-is.
"""

from __future__ import annotations

import re
from datetime import date
from typing import ClassVar

from pydantic import ConfigDict

from quor.pipeline.mask import ContentMask, Decision, LineMask
from quor.pipeline.stages._utils import _compile, apply_preserve_patterns, line_tokens
from quor.pipeline.stages.base import StageConfig

_ISO_DATE = r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
_TIME = r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
_FRAC = r"\.(?P<frac>\d{1,9})"
_OFFSET = r"(?P<offsign>[+-])(?P<offh>\d{2}):?(?P<offm>\d{2})"
_BOUNDARY = r"(?=[ \t]|$)"

# Order is irrelevant for correctness (each pattern requires a distinct
# literal character at the same fixed position, so they are mutually
# exclusive by construction) but kept most-specific-looking first for
# readability.
_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iso_frac_offset", _compile(f"^{_ISO_DATE}T{_TIME}{_FRAC}{_OFFSET}{_BOUNDARY}")),
    ("iso_offset", _compile(f"^{_ISO_DATE}T{_TIME}{_OFFSET}{_BOUNDARY}")),
    ("iso_frac_z", _compile(f"^{_ISO_DATE}T{_TIME}{_FRAC}Z{_BOUNDARY}")),
    ("iso_z", _compile(f"^{_ISO_DATE}T{_TIME}Z{_BOUNDARY}")),
    ("space_datetime", _compile(f"^{_ISO_DATE} {_TIME}{_BOUNDARY}")),
    ("time_frac", _compile(f"^{_TIME}{_FRAC}{_BOUNDARY}")),
    ("time_only", _compile(f"^{_TIME}{_BOUNDARY}")),
)

_NS_PER_UNIT: tuple[tuple[str, int], ...] = (
    ("h", 3_600_000_000_000),
    ("m", 60_000_000_000),
    ("s", 1_000_000_000),
    ("ms", 1_000_000),
    ("us", 1_000),
    ("ns", 1),
)


class _Parsed:
    __slots__ = ("end", "frac_width", "kind", "ns")

    def __init__(self, kind: str, end: int, frac_width: int, ns: int) -> None:
        self.kind = kind
        self.end = end
        self.frac_width = frac_width
        self.ns = ns


def _parse_line(line: str) -> _Parsed | None:
    """Return the parsed timestamp at the start of `line`, or None if no
    supported format matches or the matched text isn't a valid date/time
    (e.g. hour 25, month 13) — "every timestamp parses successfully" (QB-098)
    is enforced here, not assumed from the regex shape alone."""
    for kind, pattern in _FORMATS:
        m = pattern.match(line)
        if m is None:
            continue

        hour = int(m.group("hour"))
        minute = int(m.group("minute"))
        second = int(m.group("second"))
        if hour > 23 or minute > 59 or second > 59:
            return None

        frac = m.groupdict().get("frac")
        frac_width = len(frac) if frac else 0
        frac_ns = int(frac.ljust(9, "0")) if frac else 0
        time_ns = (hour * 3600 + minute * 60 + second) * 1_000_000_000 + frac_ns

        if kind in ("time_only", "time_frac"):
            return _Parsed(kind, m.end(), frac_width, time_ns)

        year = int(m.group("year"))
        month = int(m.group("month"))
        day = int(m.group("day"))
        try:
            days = date(year, month, day).toordinal()
        except ValueError:
            return None
        total_ns = days * 86_400_000_000_000 + time_ns

        if kind in ("iso_offset", "iso_frac_offset"):
            offh = int(m.group("offh"))
            offm = int(m.group("offm"))
            if offh > 23 or offm > 59:
                return None
            offset_ns = (offh * 3600 + offm * 60) * 1_000_000_000
            if m.group("offsign") == "+":
                total_ns -= offset_ns
            else:
                total_ns += offset_ns

        return _Parsed(kind, m.end(), frac_width, total_ns)

    return None


def _format_delta_ns(delta_ns: int) -> str:
    """Render a non-negative nanosecond delta using the largest unit that
    divides it evenly. `ns` always divides evenly, so this always
    terminates; no rounding, no floats, ever."""
    if delta_ns == 0:
        return "+0s"
    for suffix, unit_ns in _NS_PER_UNIT:
        if delta_ns % unit_ns == 0:
            return f"+{delta_ns // unit_ns}{suffix}"
    raise AssertionError("unreachable: nanoseconds divide every integer")


class RelativeTimestampCompressionConfig(StageConfig):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RelativeTimestampCompressionStage:
    """Rewrite consecutive timestamp-prefixed KEEP lines to per-line deltas."""

    api_version: ClassVar[int] = 1
    stage_type: ClassVar[str] = "relative_timestamp_compression"

    def can_handle(self, content: str, content_type: str) -> bool:
        return True

    def apply(self, mask: ContentMask, config: StageConfig) -> ContentMask:
        if not isinstance(config, RelativeTimestampCompressionConfig):
            raise TypeError(
                "relative_timestamp_compression requires "
                f"RelativeTimestampCompressionConfig, got {type(config).__name__}"
            )

        lines = apply_preserve_patterns(list(mask.lines), config.preserve_patterns, self.stage_type)

        result: list[LineMask] = []
        i = 0
        n = len(lines)

        while i < n:
            lm = lines[i]
            parsed = _parse_line(lm.line) if lm.decision is Decision.KEEP else None
            if parsed is None:
                result.append(lm)
                i += 1
                continue

            run: list[tuple[LineMask, int, int]] = [(lm, parsed.end, parsed.ns)]
            kind = parsed.kind
            frac_width = parsed.frac_width
            prev_ns = parsed.ns
            j = i + 1

            while j < n:
                nxt = lines[j]
                if nxt.decision is not Decision.KEEP:
                    break
                nxt_parsed = _parse_line(nxt.line)
                if (
                    nxt_parsed is None
                    or nxt_parsed.kind != kind
                    or nxt_parsed.frac_width != frac_width
                    or nxt_parsed.ns < prev_ns
                ):
                    break
                run.append((nxt, nxt_parsed.end, nxt_parsed.ns))
                prev_ns = nxt_parsed.ns
                j += 1

            result.extend(_fold_run(run, self.stage_type))
            i = j

        return ContentMask(tuple(result))


def _fold_run(run: list[tuple[LineMask, int, int]], stage_type: str) -> list[LineMask]:
    """Fold `run` (2+ consecutive, same-kind, same-frac-width, non-decreasing
    timestamped KEEP LineMasks) to the first line unchanged plus every other
    line rewritten to a `+delta` prefix, if doing so is estimated to cost
    strictly fewer tokens than leaving it as-is."""
    if len(run) < 2:
        return [lm for lm, _, _ in run]

    first_lm = run[0][0]
    folded_texts = [first_lm.line]
    for k in range(1, len(run)):
        lm, end, ns = run[k]
        delta = _format_delta_ns(ns - run[k - 1][2])
        folded_texts.append(f"{delta}{lm.line[end:]}")

    original_cost = sum(line_tokens(lm.line) for lm, _, _ in run)
    folded_cost = sum(line_tokens(t) for t in folded_texts)
    if folded_cost >= original_cost:
        return [lm for lm, _, _ in run]

    result = [first_lm]
    for k in range(1, len(run)):
        lm = run[k][0]
        result.append(
            LineMask(
                line=folded_texts[k],
                decision=Decision.KEEP,
                reason=f"relative timestamp: {folded_texts[k].split(' ', 1)[0]} since previous line",
                stage=stage_type,
            )
        )
    return result
