from collections.abc import Callable

import pandas as pd
import pytest

from dbdelay.data.schemas import LATE_THRESHOLD_MIN, SILVER_SCHEMA_VERSION, validate_silver
from dbdelay.errors import DataValidationError


def _ts(*values: str | None) -> pd.Series:
    """UTC timestamps with microsecond unit, as the contract requires."""
    return pd.to_datetime(pd.Series(values, dtype="object"), utc=True).dt.as_unit("us")


def _valid_frame() -> pd.DataFrame:
    """Three departures: on time, late, cancelled."""
    return pd.DataFrame(
        {
            "event_id": ["a" * 40, "b" * 40, "c" * 40],
            "eva": ["8000105", "8000105", "8010101"],
            "station_name": ["Frankfurt (Main) Hbf", "Frankfurt (Main) Hbf", "Erfurt Hbf"],
            "ride_id": [
                "-3640391175194580346-2609251715",
                "7895304813024846263-2609251542",
                "4992819178731237054-2609251622",
            ],
            "stop_index": pd.Series([1, 15, 7], dtype="int16"),
            "train_type": ["RB", "RE", "ICE"],
            "train_number": ["15675", "4529", None],
            "line_number": ["RB61", "RE50", None],
            "final_destination": ["Dieburg", "Frankfurt(Main)Hbf", None],
            "planned_departure_utc": _ts(
                "2026-09-25 15:15", "2026-09-25 15:35", "2026-09-25 15:40"
            ),
            "changed_departure_utc": _ts("2026-09-25 15:15", "2026-09-25 15:43", None),
            "delay_min": pd.Series([0, 8, None], dtype="Int16"),
            "is_cancelled": [False, False, True],
            "is_late": pd.Series([False, True, None], dtype="boolean"),
            "source": ["live", "live", "hf"],
            "ingested_at": _ts("2026-09-25 16:00", "2026-09-25 16:00", "2026-09-25 16:00"),
        }
    )


def test_valid_frame_passes_unchanged() -> None:
    df = _valid_frame()

    result = validate_silver(df)

    pd.testing.assert_frame_equal(result, df)


def test_empty_frame_with_contract_dtypes_passes() -> None:
    df = _valid_frame().iloc[0:0]

    assert validate_silver(df).empty


def test_constants_match_the_frozen_contract() -> None:
    # Changing either one is a contract change (docs/rules.md §1).
    assert SILVER_SCHEMA_VERSION == 1
    assert LATE_THRESHOLD_MIN == 6


def _drop_column(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=["ride_id"])


def _extra_column(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(actual_delay=1)


def _naive_timestamp(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(planned_departure_utc=df["planned_departure_utc"].dt.tz_localize(None))


def _null_planned_departure(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "planned_departure_utc"] = pd.NaT
    return df


def _duplicate_event_id(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[1, "event_id"] = df.loc[0, "event_id"]
    return df


def _bad_event_id(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "event_id"] = "not-a-sha1"
    return df


def _eva_with_leading_zero(df: pd.DataFrame) -> pd.DataFrame:
    # HF stores "08000105"; silver uses the API form "8000105".
    df.loc[0, "eva"] = "08000105"
    return df


def _ride_id_without_start_time(df: pd.DataFrame) -> pd.DataFrame:
    # HF `train_line_ride_id` alone is reused across days → not a ride key.
    df.loc[0, "ride_id"] = "-3640391175194580346"
    return df


def _negative_stop_index(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "stop_index"] = -1
    return df


def _lowercase_train_type(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "train_type"] = "erx"
    return df


def _non_ascii_lowercase_train_type(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "train_type"] = "RBü"  # only the non-ASCII letter is lowercase
    return df


def _unknown_source(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "source"] = "csv"
    return df


def _cancelled_but_labelled(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[2, "is_late"] = False
    return df


def _cancelled_with_delay(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[2, "delay_min"] = 3
    return df


def _label_disagrees_with_delay(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[1, "is_late"] = False  # 8 min late but labelled on time
    return df


def _not_cancelled_without_delay(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "delay_min"] = pd.NA
    df.loc[0, "is_late"] = pd.NA
    return df


def _delay_disagrees_with_times(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[1, "delay_min"] = 7  # times say 8 min; label still consistent with 7
    return df


def _not_cancelled_without_changed_time(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "changed_departure_utc"] = pd.NaT
    return df


def _eva_with_trailing_newline(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "eva"] = "8000105\n"  # `$` would accept this; joins would silently miss
    return df


def _blank_station_name(df: pd.DataFrame) -> pd.DataFrame:
    df.loc[0, "station_name"] = "  "
    return df


def _wrong_int_dtype(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(stop_index=df["stop_index"].astype("int64"))


@pytest.mark.parametrize(
    ("corrupt", "expected_check"),
    [
        (_drop_column, "ride_id: column_in_dataframe"),
        (_extra_column, "actual_delay: column_in_schema"),
        (_naive_timestamp, "planned_departure_utc: dtype"),
        (_null_planned_departure, "planned_departure_utc: not_nullable"),
        (_duplicate_event_id, "event_id: field_uniqueness"),
        (_bad_event_id, "event_id: str_matches"),
        (_eva_with_leading_zero, "eva: str_matches"),
        (_eva_with_trailing_newline, "eva: str_matches"),
        (_ride_id_without_start_time, "ride_id: str_matches"),
        (_negative_stop_index, "stop_index: greater_than_or_equal_to"),
        (_blank_station_name, "station_name: str_matches"),
        (_lowercase_train_type, "train_type: is_upper_case"),
        (_non_ascii_lowercase_train_type, "train_type: is_upper_case"),
        (_unknown_source, "source: isin"),
        (_cancelled_but_labelled, "cancelled_has_no_delay_or_label"),
        (_cancelled_with_delay, "cancelled_has_no_delay_or_label"),
        (_label_disagrees_with_delay, "label_matches_delay"),
        (_not_cancelled_without_delay, "label_matches_delay"),
        (_delay_disagrees_with_times, "delay_matches_times"),
        (_not_cancelled_without_changed_time, "delay_matches_times"),
        (_wrong_int_dtype, "stop_index: dtype"),
    ],
)
def test_contract_violations_raise(
    corrupt: Callable[[pd.DataFrame], pd.DataFrame], expected_check: str
) -> None:
    with pytest.raises(DataValidationError) as exc_info:
        validate_silver(corrupt(_valid_frame()))

    assert expected_check in str(exc_info.value)


def test_late_threshold_is_inclusive() -> None:
    df = _valid_frame()
    df["changed_departure_utc"] = _ts("2026-09-25 15:15", "2026-09-25 15:41", None)  # +6 min
    df.loc[1, "delay_min"] = LATE_THRESHOLD_MIN  # exactly 6 min → late

    validate_silver(df)


def test_error_message_never_contains_row_values() -> None:
    df = _valid_frame()
    df.loc[0, "source"] = "LEAKME"
    df.loc[1, "eva"] = "08000105"

    with pytest.raises(DataValidationError) as exc_info:
        validate_silver(df)

    message = str(exc_info.value)
    assert "source: isin" in message
    assert "eva: str_matches" in message
    assert "LEAKME" not in message
    assert "08000105" not in message


def test_row_level_check_is_reported_once() -> None:
    with pytest.raises(DataValidationError) as exc_info:
        validate_silver(_label_disagrees_with_delay(_valid_frame()))

    assert str(exc_info.value).count("label_matches_delay") == 1
