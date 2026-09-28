from datetime import date

import pandas as pd
import pytest

from dbdelay.data.months import month_bounds_utc, month_days, shift_month, validate_month
from dbdelay.errors import ConfigError


@pytest.mark.parametrize("month", ["2026-01", "2025-12"])
def test_validate_month_accepts_yyyy_mm(month: str) -> None:
    assert validate_month(month) == month


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "26-01", "2026-01\n", ""])
def test_validate_month_rejects_other_strings(month: str) -> None:
    with pytest.raises(ConfigError):
        validate_month(month)


@pytest.mark.parametrize(
    ("month", "delta", "expected"),
    [("2026-01", -1, "2025-12"), ("2025-12", 1, "2026-01"), ("2026-03", 0, "2026-03")],
)
def test_shift_month_crosses_years(month: str, delta: int, expected: str) -> None:
    assert shift_month(month, delta) == expected


def test_month_days_covers_the_whole_month() -> None:
    days = month_days("2026-02")
    assert days[0] == date(2026, 2, 1)
    assert days[-1] == date(2026, 2, 28)
    assert len(days) == 28


def test_month_bounds_are_utc_half_open() -> None:
    start, end = month_bounds_utc("2026-03")
    assert start == pd.Timestamp("2026-03-01", tz="UTC")
    assert end == pd.Timestamp("2026-04-01", tz="UTC")
