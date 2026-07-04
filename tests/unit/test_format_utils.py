"""Unit tests for quor/cli/format_utils.py — pure presentation formatting.

These are display-only helpers (no calculation logic) used by `quor gain`.
"""

from __future__ import annotations

import pytest

from quor.cli.format_utils import format_count, format_percentage

# ---------------------------------------------------------------------------
# format_count
# ---------------------------------------------------------------------------


class TestFormatCount:
    def test_zero(self) -> None:
        assert format_count(0) == "0"

    def test_small_number_shown_as_is(self) -> None:
        assert format_count(47) == "47"

    def test_boundary_just_under_thousand(self) -> None:
        assert format_count(999) == "999"

    def test_boundary_exactly_thousand(self) -> None:
        assert format_count(1000) == "1.0k"

    def test_thousands_one_decimal(self) -> None:
        assert format_count(20100) == "20.1k"
        assert format_count(9800) == "9.8k"
        assert format_count(9700) == "9.7k"
        assert format_count(58900) == "58.9k"

    def test_boundary_just_under_million(self) -> None:
        assert format_count(999_999) == "1000.0k"

    def test_boundary_exactly_million(self) -> None:
        assert format_count(1_000_000) == "1.0M"

    def test_millions_one_decimal(self) -> None:
        assert format_count(1_234_000) == "1.2M"

    def test_boundary_exactly_billion(self) -> None:
        assert format_count(1_000_000_000) == "1.0B"

    def test_billions_one_decimal(self) -> None:
        assert format_count(2_500_000_000) == "2.5B"

    def test_negative_small_number(self) -> None:
        assert format_count(-47) == "-47"

    def test_negative_thousands(self) -> None:
        """Defensive: token deltas could theoretically be negative (a
        plugin/filter that expands content); formatting must not crash or
        drop the sign."""
        assert format_count(-20100) == "-20.1k"


# ---------------------------------------------------------------------------
# format_percentage
# ---------------------------------------------------------------------------


class TestFormatPercentage:
    def test_zero(self) -> None:
        assert format_percentage(0.0) == "0%"

    def test_one_hundred_percent(self) -> None:
        assert format_percentage(1.0) == "100%"

    def test_rounds_to_nearest_whole_percent(self) -> None:
        assert format_percentage(0.487) == "49%"
        assert format_percentage(0.483) == "48%"

    def test_small_nonzero_fraction_shows_less_than_one_percent(self) -> None:
        """A genuinely non-zero contribution that rounds to 0% must read as
        "<1%", not "0%" — otherwise a real (if tiny) saving looks like none."""
        assert format_percentage(0.0023) == "<1%"

    def test_exactly_one_percent_not_less_than(self) -> None:
        assert format_percentage(0.01) == "1%"

    def test_half_percent_boundary(self) -> None:
        assert format_percentage(0.005) == "<1%"

    @pytest.mark.parametrize("fraction", [0.0, 0.005, 0.01, 0.5, 0.8, 1.0])
    def test_never_raises(self, fraction: float) -> None:
        format_percentage(fraction)  # must not raise for any valid fraction
