"""Month strings ("YYYY-MM") that address HF files and silver partitions."""

import re
from datetime import date, timedelta

import pandas as pd

from dbdelay.errors import ConfigError

_MONTH = re.compile(r"(20[0-9]{2})-(0[1-9]|1[0-2])")


def validate_month(month: str) -> str:
    """Return ``month`` unchanged if it is ``YYYY-MM``.

    Raises:
        ConfigError: for anything else.
    """
    if not _MONTH.fullmatch(month):
        raise ConfigError(f"month must look like YYYY-MM, got {month!r}")
    return month


def shift_month(month: str, delta: int) -> str:
    """Return the month ``delta`` months after ``month`` (negative = before)."""
    year, mon = (int(part) for part in validate_month(month).split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_days(month: str) -> list[date]:
    """All calendar days of ``month``."""
    first = date.fromisoformat(f"{validate_month(month)}-01")
    following = date.fromisoformat(f"{shift_month(month, 1)}-01")
    return [first + timedelta(days=offset) for offset in range((following - first).days)]


def month_bounds_utc(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Half-open UTC interval ``[first day 00:00, next month's first day 00:00)``."""
    start = pd.Timestamp(f"{validate_month(month)}-01", tz="UTC")
    end = pd.Timestamp(f"{shift_month(month, 1)}-01", tz="UTC")
    return start, end
