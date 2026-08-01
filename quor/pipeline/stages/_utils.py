"""Shared helpers for compression stages.

_compile:            lru_cache-backed pattern compilation (satisfies "compile
                     once at filter load time, not per line").
_search:             thin wrapper around pat.search() with timeout — exists
                     as a separate function so tests can patch it without
                     touching the C extension.
_sub:                thin wrapper around pat.sub() with timeout, for
                     regex_replace.
_fullmatch:          thin wrapper around pat.fullmatch() with timeout, for
                     match_output's whole-output matching.
matches_any:         returns True if any compiled pattern matches the line;
                     on TimeoutError it warns and moves on (fail-open).
_apply_ast_summary:  merges a language analyzer's body-compress line numbers
                     and import-block replacements (QB-096) into a new
                     ContentMask — shared by python_ast_summarize.py and
                     code_ast_summarize.py so the merge logic (PROTECT/
                     COMPRESS passthrough, preserve_patterns, import-block
                     splicing, body compression) exists exactly once, not
                     twice with two chances to drift.
line_tokens:         ceil(len(line) / 4) token-cost estimate — shared by
                     every token-cost-gated stage (`collapse_unchanged_
                     context`, `max_tokens`, `path_prefix_fold`, `numeric_
                     range_compression`, `relative_timestamp_compression`).
                     QB-098 extracted this from five identical copies once
                     a fifth stage was about to add a sixth — see
                     backlog.md's QB-098 entry ("convergence" note) for why
                     this leaf helper was worth sharing while each stage's
                     own run-detection/rendering logic deliberately stays
                     independent.
apply_preserve_patterns: the identical "PROTECT any KEEP line matching one
                     of `preserve_patterns`, pre-pass, before building runs"
                     logic shared by `path_prefix_fold`, `numeric_range_
                     compression`, and `relative_timestamp_compression` —
                     same QB-098 extraction, same reasoning.
"""

from __future__ import annotations

import functools
import math
import warnings

import regex

from quor.pipeline.ast_summarize.import_model import ImportBlockReplacement
from quor.pipeline.mask import ContentMask, Decision, LineMask

_PATTERN_TIMEOUT: float = 1.0  # seconds; override in tests via monkeypatch


@functools.lru_cache(maxsize=512)
def _compile(pattern: str) -> regex.Pattern[str]:
    """Return a compiled regex.Pattern, cached by pattern string."""
    return regex.compile(pattern)


def _search(pat: regex.Pattern[str], line: str) -> regex.Match[str] | None:
    """Call pat.search with the configured timeout.

    Exists as a named function so tests can patch it to raise TimeoutError
    without having to touch the C extension type directly.
    """
    return pat.search(line, timeout=_PATTERN_TIMEOUT)


def _sub(pat: regex.Pattern[str], repl: str, line: str) -> str:
    """Call pat.sub with the configured timeout. See _search for why this
    is a named function rather than calling pat.sub directly."""
    result: str = pat.sub(repl, line, timeout=_PATTERN_TIMEOUT)
    return result


def _fullmatch(pat: regex.Pattern[str], text: str) -> regex.Match[str] | None:
    """Call pat.fullmatch with the configured timeout. See _search for why
    this is a named function rather than calling pat.fullmatch directly."""
    return pat.fullmatch(text, timeout=_PATTERN_TIMEOUT)


def matches_any(line: str, patterns: list[regex.Pattern[str]]) -> bool:
    """Return True if `line` matches any pattern; False on timeout (with a warning)."""
    for pat in patterns:
        try:
            if _search(pat, line):
                return True
        except TimeoutError:
            warnings.warn(
                f"[quor] Pattern {pat.pattern!r} timed out matching line; skipping",
                stacklevel=3,
            )
    return False


def line_tokens(line: str) -> int:
    """Estimate a line's token cost: ceil(len(line) / 4).

    Extracted (QB-098) from five byte-identical copies (`collapse_unchanged_
    context`, `max_tokens`, `path_prefix_fold`, `numeric_range_compression`,
    `relative_timestamp_compression`) — a pure leaf function with no
    stage-specific behavior, so sharing it costs nothing in readability.
    """
    return max(1, math.ceil(len(line) / 4))


def apply_preserve_patterns(
    lines: list[LineMask], preserve_patterns: list[str], stage_type: str
) -> list[LineMask]:
    """Mark every not-already-PROTECT line matching any of `preserve_patterns`
    as PROTECT; every other line is returned unchanged.

    Extracted (QB-098) from three byte-identical copies (`path_prefix_fold`,
    `numeric_range_compression`, `relative_timestamp_compression`) — same
    "pre-pass before building runs" convention `group_repeated`/`strip_lines`
    also use, but those two have their own, slightly different `not
    Decision.COMPRESS`-agnostic behavior (see `quor/pipeline/engine.py`'s
    early-exit docstring) and are deliberately NOT folded into this helper —
    only the three genuinely identical copies were unified.
    """
    if not preserve_patterns:
        return lines
    compiled = [_compile(p) for p in preserve_patterns]
    return [
        LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", stage_type)
        if lm.decision is not Decision.PROTECT and matches_any(lm.line, compiled)
        else lm
        for lm in lines
    ]


def _apply_ast_summary(
    mask: ContentMask,
    compress_lines: set[int],
    import_replacements: list[ImportBlockReplacement],
    compiled_preserve: list[regex.Pattern[str]],
    stage_type: str,
    body_reason: str,
) -> ContentMask:
    """Return a new ContentMask combining `compress_lines` (function/method
    body lines to COMPRESS, exactly as before QB-096) with
    `import_replacements` (QB-096 import-block collapsing).

    Order of operations, and why:
    1. `preserve_patterns` is applied first, as its own pass over the
       original lines — same "pre-pass, not interleaved" convention
       `group_repeated`/`strip_lines`/`path_prefix_fold` already use, so a
       line a filter author explicitly protects is PROTECT before anything
       else looks at it.
    2. Any `import_replacements` entry whose line range now overlaps a
       newly-PROTECTed line is dropped entirely — not partially applied,
       not shrunk to avoid the protected line, the whole run is left
       unfolded. `import_replacements` is computed by a language analyzer
       with no knowledge of this filter's `preserve_patterns` at all (it
       operates on raw source text, not a ContentMask), so this is the
       merge point where that conflict must be resolved — conservatively,
       per this project's standing "when uncertain, don't collapse" rule
       (the same one `path_prefix_fold`'s token-cost gate already leans on
       when in doubt, applied here to a correctness conflict instead of a
       cost one).
    3. Every surviving replacement's first line is rewritten to its
       (possibly multi-line) synthesized text and kept; every other line in
       its range is COMPRESSed — the same "rewrite first line, compress the
       rest" technique `group_repeated`/`collapse_unchanged_context` already
       use, extended by `path_prefix_fold` to rewrite every line in a run
       instead of just the first, and by this function to rewrite the first
       line to a block that itself contains embedded newlines (see
       `quor/pipeline/mask.py`'s module docstring for the exact, narrow
       invariant this represents for `python_ast_summarize`/
       `code_ast_summarize`).
    4. Everything else falls through to the pre-QB-096 behavior: a
       `compress_lines` line is COMPRESSed with `body_reason`; anything else
       is left exactly as it was.
    """
    lines = list(mask.lines)

    protected_line_numbers: set[int] = set()
    if compiled_preserve:
        pass1: list[LineMask] = []
        for idx, lm in enumerate(lines):
            if lm.decision is not Decision.PROTECT and matches_any(lm.line, compiled_preserve):
                pass1.append(LineMask(lm.line, Decision.PROTECT, "matches preserve_pattern", stage_type))
                protected_line_numbers.add(idx + 1)
            else:
                pass1.append(lm)
        lines = pass1

    valid_replacements = [
        r
        for r in import_replacements
        if not protected_line_numbers & set(range(r.start_line, r.end_line + 1))
    ]
    replacement_by_start = {r.start_line: r for r in valid_replacements}
    replacement_lines: set[int] = set()
    for r in valid_replacements:
        replacement_lines.update(range(r.start_line, r.end_line + 1))

    result: list[LineMask] = []
    for idx, lm in enumerate(lines):
        line_number = idx + 1

        if lm.decision is Decision.PROTECT:
            result.append(lm)
            continue
        if lm.decision is Decision.COMPRESS:
            result.append(lm)
            continue

        replacement = replacement_by_start.get(line_number)
        if replacement is not None:
            result.append(LineMask(replacement.text, Decision.KEEP, "collapsed import block", stage_type))
            continue
        if line_number in replacement_lines:
            result.append(LineMask(lm.line, Decision.COMPRESS, "collapsed import block", stage_type))
            continue

        if line_number in compress_lines:
            result.append(LineMask(lm.line, Decision.COMPRESS, body_reason, stage_type))
        else:
            result.append(lm)

    return ContentMask(tuple(result))
