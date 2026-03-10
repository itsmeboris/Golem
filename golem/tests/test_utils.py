# pylint: disable=too-few-public-methods
"""Tests for golem.utils — shared utility functions."""

from __future__ import annotations

import pytest

from golem.utils import format_duration


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0, "0s"),
            (-5, "0s"),
            (-0.001, "0s"),
            (0.5, "< 1s"),
            (0.99, "< 1s"),
            (1.0, "1s"),
            (45, "45s"),
            (59, "59s"),
            (59.9, "59s"),
            (60, "1m 0s"),
            (150, "2m 30s"),
            (3599, "59m 59s"),
            (3600, "1h 0m 0s"),
            (4542, "1h 15m 42s"),
            (86400, "24h 0m 0s"),
        ],
    )
    def test_format_duration(self, seconds: float, expected: str) -> None:
        assert format_duration(seconds) == expected
