"""Presentation-only formatting helpers for CLI output.

Pure functions, no I/O, no business logic — safe to unit test in isolation
and reuse across CLI commands. Keeping this separate from quor/tracking/db.py
(the calculation layer) is deliberate: this module must never influence what
gets computed, only how an already-computed number is displayed.
"""

from __future__ import annotations


def format_count(value: int) -> str:
    """Format an integer count/token value for compact CLI display.

    Values under 1000 are shown as-is. Larger values are abbreviated to one
    decimal place with a k/M/B suffix (e.g. 20100 -> "20.1k", 1_234_000 ->
    "1.2M"). Purely cosmetic formatting — never used in any calculation.
    """
    magnitude = abs(value)
    sign = "-" if value < 0 else ""

    if magnitude < 1_000:
        return str(value)
    if magnitude < 1_000_000:
        return f"{sign}{magnitude / 1_000:.1f}k"
    if magnitude < 1_000_000_000:
        return f"{sign}{magnitude / 1_000_000:.1f}M"
    return f"{sign}{magnitude / 1_000_000_000:.1f}B"


def format_percentage(fraction: float) -> str:
    """Format a 0..1 fraction as a rounded whole-percent string.

    A fraction that rounds to 0% but is genuinely non-zero is shown as
    "<1%" rather than "0%", so a small-but-real contribution isn't
    misread as no contribution at all.
    """
    percent = fraction * 100
    if 0 < percent < 1:
        return "<1%"
    return f"{round(percent)}%"


def format_duration(seconds: float) -> str:
    """Format a non-negative duration in seconds as a compact "N unit(s)"
    string (e.g. "3 minutes", "2 days") — the largest whole unit that fits,
    never combined with a smaller one. Purely cosmetic, like
    `format_count`/`format_percentage` — never used in any calculation.
    """
    seconds = max(0.0, seconds)
    if seconds < 60:
        n = round(seconds)
        return f"{n} second{'s' if n != 1 else ''}"
    minutes = seconds / 60
    if minutes < 60:
        n = round(minutes)
        return f"{n} minute{'s' if n != 1 else ''}"
    hours = minutes / 60
    if hours < 24:
        n = round(hours)
        return f"{n} hour{'s' if n != 1 else ''}"
    days = hours / 24
    n = round(days)
    return f"{n} day{'s' if n != 1 else ''}"
