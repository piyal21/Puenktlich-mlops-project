import pandas as pd

from dbdelay.features.calendar import is_public_holiday, local_parts


def _utc(*stamps: str) -> pd.Series:
    return pd.Series(pd.to_datetime(list(stamps), utc=True))


def test_local_parts_in_winter_and_summer() -> None:
    parts = local_parts(_utc("2026-03-10 07:15", "2026-07-04 10:00"))
    assert parts["hour_local"].tolist() == [8, 12]
    assert parts["minute_of_day"].tolist() == [8 * 60 + 15, 12 * 60]
    assert parts["weekday"].tolist() == [1, 5]
    assert parts["is_weekend"].tolist() == [False, True]
    assert parts["month"].tolist() == [3, 7]
    assert parts.dtypes.astype(str).to_dict() == {
        "hour_local": "int8",
        "minute_of_day": "int16",
        "weekday": "int8",
        "is_weekend": "bool",
        "month": "int8",
    }


def test_spring_forward_skips_the_two_oclock_hour() -> None:
    assert local_parts(_utc("2026-03-29 00:30", "2026-03-29 01:30"))["hour_local"].tolist() == [
        1,
        3,
    ]


def test_autumn_back_repeats_the_two_oclock_hour() -> None:
    parts = local_parts(_utc("2025-10-26 00:30", "2025-10-26 01:30"))
    assert parts["hour_local"].tolist() == [2, 2]
    assert parts["minute_of_day"].tolist() == [150, 150]


def test_local_date_can_differ_from_utc_date() -> None:
    parts = local_parts(_utc("2026-02-28 23:30"))
    assert (parts["month"].iloc[0], parts["weekday"].iloc[0]) == (3, 6)


def test_state_holiday_only_counts_for_that_state() -> None:
    planned = _utc("2026-01-06 10:00", "2026-01-06 10:00", "2025-12-25 10:00", "2026-03-10 10:00")
    states = pd.Series(["BW", "NW", "NW", "BW"])
    assert is_public_holiday(planned, states).tolist() == [True, False, True, False]


def test_holiday_uses_the_berlin_local_date() -> None:
    assert is_public_holiday(_utc("2026-01-05 23:30"), pd.Series(["BW"])).tolist() == [True]


def test_unknown_or_missing_state_uses_national_holidays() -> None:
    planned = _utc("2025-12-25 10:00", "2026-01-06 10:00", "2026-01-06 10:00")
    states = pd.Series(["XX", None, "XX"])
    assert is_public_holiday(planned, states).tolist() == [True, False, False]


def test_empty_input_gives_empty_output() -> None:
    empty = pd.Series(pd.to_datetime([], utc=True))
    assert local_parts(empty).empty
    assert is_public_holiday(empty, pd.Series([], dtype=object)).empty
