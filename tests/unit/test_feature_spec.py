import pandas as pd
import pytest

from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError
from dbdelay.features.spec import (
    FEATURE_COLUMNS,
    FORBIDDEN_COLUMNS,
    OTHER,
    FeatureSpec,
    category_keys,
    fit_spec,
    require_columns,
)
from tests.builders import silver_frame

STATIONS = [
    Station(eva="8000105", name="Frankfurt (Main) Hbf", state="HE"),
    Station(eva="8000096", name="Stuttgart Hbf", state="BW"),
]


def _train() -> pd.DataFrame:
    frame = silver_frame(4)
    frame["train_type"] = ["RB", "RB", "RB", "ICE"]
    frame["line_number"] = ["58", "58", None, None]
    return frame


def test_constants_do_not_overlap() -> None:
    assert not set(FEATURE_COLUMNS) & set(FORBIDDEN_COLUMNS)


def test_category_keys_build_line_and_destination_keys() -> None:
    keys = category_keys(_train())
    assert keys["line_key"].tolist() == ["RB:58", "RB:58", "RB:none", "ICE:none"]
    assert keys["destination_key"].tolist() == ["Dieburg"] * 4


def test_rare_levels_become_other_and_other_is_last() -> None:
    spec = fit_spec(_train(), STATIONS, min_count=2)
    assert spec.levels["train_type"] == ("RB", OTHER)
    assert spec.levels["line_key"] == ("RB:58", OTHER)
    assert spec.levels["eva"] == ("8000105", OTHER)
    assert spec.station_states == {"8000105": "HE", "8000096": "BW"}


def test_json_round_trip_and_stable_hash() -> None:
    spec = fit_spec(_train(), STATIONS, min_count=1)
    again = FeatureSpec.from_json(spec.to_json())
    assert again == spec
    assert again.spec_hash == spec.spec_hash
    assert len(spec.spec_hash) == 64


def test_bad_spec_json_raises() -> None:
    with pytest.raises(DataValidationError):
        FeatureSpec.from_json('{"min_count": "many"}')


def test_missing_column_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="final_destination"):
        require_columns(_train().drop(columns=["final_destination"]))


def test_naive_timestamps_are_rejected() -> None:
    frame = _train()
    frame["planned_departure_utc"] = frame["planned_departure_utc"].dt.tz_localize(None)
    with pytest.raises(DataValidationError, match="timezone"):
        require_columns(frame)


def test_empty_train_cannot_be_fitted() -> None:
    with pytest.raises(DataValidationError):
        fit_spec(_train().iloc[0:0], STATIONS, min_count=1)


def test_null_stop_index_is_rejected() -> None:
    frame = _train()
    frame["stop_index"] = pd.Series([1, None, 2, 3], dtype="Int16")
    with pytest.raises(DataValidationError, match="stop_index"):
        require_columns(frame)


def test_out_of_range_stop_index_is_rejected() -> None:
    frame = _train()
    frame["stop_index"] = [1.0, 40000.0, 2.0, 3.0]
    with pytest.raises(DataValidationError, match="stop_index"):
        require_columns(frame)


def test_missing_planned_departure_is_rejected() -> None:
    frame = _train()
    frame.loc[1, "planned_departure_utc"] = pd.NaT
    with pytest.raises(DataValidationError, match="planned_departure_utc"):
        require_columns(frame)
