import pandas as pd
import pytest

from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FEATURE_COLUMNS, FORBIDDEN_COLUMNS, OTHER, fit_spec
from tests.builders import silver_frame

STATIONS = [Station(eva="8000105", name="Frankfurt (Main) Hbf", state="BW")]


def _frame() -> pd.DataFrame:
    return silver_frame(3)


def test_columns_order_and_dtypes() -> None:
    frame = _frame()
    features = build_features(frame, fit_spec(frame, STATIONS, min_count=1))
    assert tuple(features.columns) == FEATURE_COLUMNS
    assert features.dtypes.astype(str).tolist() == [
        "category",
        "category",
        "category",
        "category",
        "int16",
        "int8",
        "int16",
        "int8",
        "bool",
        "int8",
        "bool",
    ]


def test_unseen_level_maps_to_other() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    frame.loc[0, "train_type"] = "ICE"
    features = build_features(frame, spec)
    assert features["train_type"].tolist() == [OTHER, "RB", "RB"]
    assert list(features["train_type"].cat.categories) == list(spec.levels["train_type"])


def test_holiday_uses_the_spec_station_state() -> None:
    frame = _frame()
    frame["planned_departure_utc"] = pd.Series(
        pd.to_datetime(["2026-01-06 10:00"] * 3, utc=True), dtype="datetime64[us, UTC]"
    )
    features = build_features(frame, fit_spec(frame, STATIONS, min_count=1))
    assert features["is_public_holiday"].tolist() == [True, True, True]


def test_deterministic() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    pd.testing.assert_frame_equal(build_features(frame, spec), build_features(frame, spec))


def test_duplicate_index_is_kept_row_for_row() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    frame.index = pd.Index([7, 7, 3])
    frame.loc[3, "stop_index"] = 9
    features = build_features(frame, spec)
    assert len(features) == 3
    assert features.index.tolist() == [7, 7, 3]
    assert features["stop_index"].tolist() == [1, 1, 9]


def test_missing_input_column_raises() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    with pytest.raises(DataValidationError):
        build_features(frame.drop(columns=["stop_index"]), spec)


def test_leakage_guard_output_has_no_forbidden_columns() -> None:
    frame = _frame()
    features = build_features(frame, fit_spec(frame, STATIONS, min_count=1))
    assert not set(features.columns) & set(FORBIDDEN_COLUMNS)


def test_leakage_guard_forbidden_columns_cannot_change_features() -> None:
    frame = _frame()
    spec = fit_spec(frame, STATIONS, min_count=1)
    expected = build_features(frame, spec)

    scrambled = frame.copy()
    scrambled["changed_departure_utc"] = scrambled["planned_departure_utc"] + pd.to_timedelta(
        [90, 5, 0], unit="m"
    )
    scrambled["delay_min"] = pd.Series([90, 5, 0], dtype="Int16")
    scrambled["is_late"] = pd.Series([True, False, True], dtype="boolean")
    scrambled["is_cancelled"] = [True, True, False]
    scrambled["event_id"] = ["x", "y", "z"]
    scrambled["ride_id"] = ["r", "s", "t"]
    scrambled["ingested_at"] = scrambled["ingested_at"].iloc[::-1].to_numpy()
    scrambled["source"] = "live"
    pd.testing.assert_frame_equal(build_features(scrambled, spec), expected)
    pd.testing.assert_frame_equal(
        build_features(frame.drop(columns=list(FORBIDDEN_COLUMNS)), spec), expected
    )
