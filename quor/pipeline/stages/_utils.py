"""Shared helpers for compression stages.

_compile:      lru_cache-backed pattern compilation (satisfies "compile once at
               filter load time, not per line").
_search:       thin wrapper around pat.search() with timeout — exists as a
               separate function so tests can patch it without touching the C
               extension.
matches_any:   returns True if any compiled pattern matches the line;
               on TimeoutError it warns and moves on (fail-open).
"""

from __future__ import annotations

import functools
import warnings

import regex

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
