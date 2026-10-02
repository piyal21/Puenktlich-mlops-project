"""Berlin local-time parts and German public holidays for planned departures."""

from functools import lru_cache

import holidays
import pandas as pd

BERLIN = "Europe/Berlin"
_SATURDAY = 5
_NATIONAL = ""


def local_parts(planned_utc: pd.Series) -> pd.DataFrame:
    """Hour, minute of day, weekday (0 = Monday), weekend flag and month in Europe/Berlin."""
    local = planned_utc.dt.tz_convert(BERLIN)
    hour = local.dt.hour
    weekday = local.dt.weekday
    return pd.DataFrame(
        {
            "hour_local": hour.astype("int8"),
            "minute_of_day": (hour * 60 + local.dt.minute).astype("int16"),
            "weekday": weekday.astype("int8"),
            "is_weekend": (weekday >= _SATURDAY).astype("bool"),
            "month": local.dt.month.astype("int8"),
        },
        index=planned_utc.index,
    )


@lru_cache(maxsize=64)
def _holiday_days(state: str, years: tuple[int, ...]) -> pd.DatetimeIndex:
    subdivisions = holidays.country_holidays("DE").subdivisions
    subdiv = state if state in subdivisions else None
    calendar = holidays.country_holidays("DE", subdiv=subdiv, years=years)
    return pd.DatetimeIndex(sorted(calendar.keys()))


def is_public_holiday(planned_utc: pd.Series, states: pd.Series) -> "pd.Series[bool]":
    """True on a national holiday or a holiday of the row's state (Berlin local date).

    ``states`` holds German state codes (e.g. ``"BW"``); unknown or missing codes use the
    national calendar only.
    """
    local_day = planned_utc.dt.tz_convert(BERLIN).dt.tz_localize(None).dt.normalize()
    years = tuple(sorted({int(year) for year in local_day.dt.year.unique()}))
    keys = states.fillna(_NATIONAL).astype(str).to_numpy()
    result = pd.Series(False, index=planned_utc.index, dtype="bool")
    for state in sorted(set(keys.tolist())):
        mask = keys == state
        result[mask] = local_day[mask].isin(_holiday_days(state, years)).to_numpy()
    return result
