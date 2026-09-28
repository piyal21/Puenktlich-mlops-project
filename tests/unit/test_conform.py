from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from dbdelay.data.silver import conform_hf, make_event_id
from dbdelay.data.stations import load_stations
from dbdelay.errors import DataValidationError
from tests.builders import hf_frame, hf_row

STATIONS = load_stations(Path(__file__).resolve().parents[2] / "configs" / "stations.yaml")


def _one(month: str = "2026-03", **overrides: object) -> pd.Series:
    result = conform_hf(hf_frame(hf_row(**overrides)), STATIONS, month)
    assert len(result.silver) == 1, (result.drops, result.quarantine)
    return result.silver.iloc[0]


def test_eva_without_leading_zero_and_station_name_from_config() -> None:
    row = _one()
    assert row["eva"] == "8000105"
    assert row["station_name"] == "Frankfurt (Main) Hbf"


def test_alias_eva_maps_to_canonical_station() -> None:
    row = _one(eva="08011160")
    assert row["eva"] == "8098160"
    assert row["station_name"] == "Berlin Hauptbahnhof"


def test_unsupported_station_is_dropped_and_counted() -> None:
    result = conform_hf(hf_frame(hf_row(eva="08000001")), STATIONS, "2026-03")
    assert result.silver.empty
    assert result.drops["not_supported_station"] == 1


def test_ride_id_and_stop_index_come_from_the_id() -> None:
    row = _one(id="-3640391175194580346-2603100815-12")
    assert row["ride_id"] == "-3640391175194580346-2603100815"
    assert row["stop_index"] == 12


def test_event_id_uses_the_frozen_format() -> None:
    row = _one()
    assert row["event_id"] == make_event_id(
        "8000105", "-3640391175194580346-2603100815", pd.Timestamp("2026-03-10 07:15", tz="UTC")
    )


@pytest.mark.parametrize(
    ("local", "utc"),
    [
        (datetime(2026, 3, 10, 8, 15), "2026-03-10 07:15"),  # CET, +1
        (datetime(2026, 3, 30, 8, 15), "2026-03-30 06:15"),  # CEST, +2
    ],
)
def test_planned_time_is_converted_to_utc(local: datetime, utc: str) -> None:
    row = _one(departure_planned_time=local, departure_change_time=local)
    assert row["planned_departure_utc"] == pd.Timestamp(utc, tz="UTC")
    assert str(row["planned_departure_utc"].unit) == "us"


@pytest.mark.parametrize(
    ("month", "local"),
    [
        ("2025-10", datetime(2025, 10, 26, 2, 30)),  # autumn: 02:30 happens twice
        ("2026-03", datetime(2026, 3, 29, 2, 30)),  # spring: 02:30 does not exist
    ],
)
def test_dst_edge_times_are_dropped_and_counted(month: str, local: datetime) -> None:
    raw = hf_frame(
        hf_row(departure_planned_time=local, departure_change_time=local, _file_month=month)
    )
    result = conform_hf(raw, STATIONS, month)
    assert result.silver.empty
    assert result.drops["dst_ambiguous_or_nonexistent"] == 1
    assert result.quarantine.empty


def test_dst_straddling_delay_is_quarantined() -> None:
    # HF computes delay on naive local times: 01:50 → 03:05 = 75, but only 15 real minutes.
    raw = hf_frame(
        hf_row(
            departure_planned_time=datetime(2026, 3, 29, 1, 50),
            departure_change_time=datetime(2026, 3, 29, 3, 5),
            delay_in_min=75,
        )
    )
    result = conform_hf(raw, STATIONS, "2026-03")
    assert result.silver.empty
    assert result.quarantine["quarantine_reason"].tolist() == ["delay_mismatch_utc"]


def test_cancelled_has_no_delay_or_label_but_keeps_changed_time() -> None:
    row = _one(
        departure_is_canceled=True,
        delay_in_min=12,
        departure_change_time=datetime(2026, 3, 10, 8, 27),
    )
    assert pd.isna(row["delay_min"])
    assert pd.isna(row["is_late"])
    assert row["is_cancelled"]
    assert row["changed_departure_utc"] == pd.Timestamp("2026-03-10 07:27", tz="UTC")


@pytest.mark.parametrize(("delay", "late"), [(-1, False), (5, False), (6, True), (40, True)])
def test_label_threshold(delay: int, late: bool) -> None:
    planned = datetime(2026, 3, 10, 8, 15)
    row = _one(delay_in_min=delay, departure_change_time=planned + timedelta(minutes=delay))
    assert row["delay_min"] == delay
    assert row["is_late"] == late


def test_train_type_is_stripped_and_upper_cased() -> None:
    assert _one(train_type=" erx ")["train_type"] == "ERX"


def test_null_strings_stay_none() -> None:
    row = _one(line_number=None, final_destination_station=None)
    assert row["line_number"] is None
    assert row["final_destination"] is None


def test_rows_are_kept_by_utc_date_of_the_month() -> None:
    inside = hf_row(
        id="1-2604010030-1",
        departure_planned_time=datetime(2026, 4, 1, 0, 30),
        departure_change_time=datetime(2026, 4, 1, 0, 30),
        _file_month="2026-04",
    )
    outside = hf_row(
        id="2-2603010030-1",
        departure_planned_time=datetime(2026, 3, 1, 0, 30),
        departure_change_time=datetime(2026, 3, 1, 0, 30),
    )
    result = conform_hf(hf_frame(inside, outside), STATIONS, "2026-03")
    # 2026-04-01 00:30 CEST = 2026-03-31 22:30 UTC (in March); 2026-03-01 00:30 CET = Feb 28 UTC.
    assert result.silver["ride_id"].tolist() == ["1-2604010030"]
    assert result.drops["out_of_month"] == 1


def test_duplicates_keep_the_latest_ingested_copy() -> None:
    older = hf_row(ingested_at=datetime(2026, 9, 1, tzinfo=UTC), train_number="old")
    newer = hf_row(
        ingested_at=datetime(2026, 9, 2, tzinfo=UTC), train_number="new", _file_month="2026-04"
    )
    result = conform_hf(hf_frame(older, newer), STATIONS, "2026-03")
    assert result.silver["train_number"].tolist() == ["new"]
    assert result.drops["duplicate"] == 1


def test_quarantine_only_keeps_rows_of_this_month() -> None:
    raw = hf_frame(
        hf_row(train_type=None),
        hf_row(
            train_type=None,
            id="9-2604150815-1",
            departure_planned_time=datetime(2026, 4, 15, 8, 15),
            _file_month="2026-04",
        ),
    )
    result = conform_hf(raw, STATIONS, "2026-03")
    assert len(result.quarantine) == 1


def test_missing_input_column_fails_loudly() -> None:
    with pytest.raises(DataValidationError, match="line_number"):
        conform_hf(hf_frame(hf_row()).drop(columns="line_number"), STATIONS, "2026-03")


def test_empty_input_gives_empty_valid_silver() -> None:
    result = conform_hf(hf_frame(), STATIONS, "2026-03")
    assert result.silver.empty
    assert result.silver.columns[0] == "event_id"
