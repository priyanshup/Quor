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
