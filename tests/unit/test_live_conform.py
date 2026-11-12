from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from dbdelay.data.schemas import validate_silver
from dbdelay.data.silver import SILVER_COLUMNS, make_event_id
from dbdelay.data.stations import Station
from dbdelay.live.conform import StopMerger, final_destination, to_silver, via
from dbdelay.timetables.parser import StopEvent

DAY = date(2026, 11, 12)
PLANNED = datetime(2026, 11, 12, 15, 5, tzinfo=UTC)
SEEN = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
STATIONS = [Station(eva="8000199", name="Kiel Hbf", state="SH")]


def event(**overrides: Any) -> StopEvent:
    fields: dict[str, Any] = {
        "stop_id": "-5-2611121400-3",
        "ride_id": "-5-2611121400",
        "stop_index": 3,
        "eva": "8000199",
        "train_type": "RE",
        "train_number": "4711",
        "line_number": "RE7",
        "planned_utc": PLANNED,
        "changed_utc": None,
        "cancelled": False,
        "planned_platform": "2",
        "changed_platform": None,
        "planned_path": ("Neumünster", "Hamburg Hbf"),
        "changed_path": None,
        "distant_endpoint": None,
    }
    return StopEvent(**(fields | overrides))


def change(**overrides: Any) -> StopEvent:
    # fchg usually lacks the planned time and train info.
    base = {"planned_utc": None, "train_type": None, "train_number": None, "planned_path": ()}
    return event(**(base | overrides))


def silver_of(merger: StopMerger) -> pd.DataFrame:
    frame, _ = to_silver(merger.stops(), STATIONS, DAY)
    return frame


def test_no_change_means_on_time() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    row = silver_of(merger).iloc[0]
    assert row["delay_min"] == 0
    assert not row["is_late"]
    assert row["changed_departure_utc"] == pd.Timestamp(PLANNED)
    assert row["event_id"] == make_event_id("8000199", "-5-2611121400", pd.Timestamp(PLANNED))
    assert row["station_name"] == "Kiel Hbf"
    assert row["source"] == "live"
    assert row["final_destination"] == "Hamburg Hbf"


def test_delay_7_is_late() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    merger.add_change(change(changed_utc=PLANNED + timedelta(minutes=7)), SEEN)
    row = silver_of(merger).iloc[0]
    assert row["delay_min"] == 7
    assert row["is_late"]


def test_latest_change_wins_reinstated_train() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    merger.add_change(change(cancelled=True, changed_path=()), SEEN + timedelta(minutes=15))
    merger.add_change(change(changed_utc=PLANNED), SEEN + timedelta(minutes=30))
    row = silver_of(merger).iloc[0]
    assert not row["is_cancelled"]
    assert row["delay_min"] == 0


def test_older_change_does_not_replace_newer() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    merger.add_change(change(changed_utc=PLANNED + timedelta(minutes=9)), SEEN + timedelta(hours=1))
    merger.add_change(change(changed_utc=PLANNED + timedelta(minutes=2)), SEEN)
    assert silver_of(merger).iloc[0]["delay_min"] == 9


def test_cancelled_has_null_label() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    merger.add_change(change(cancelled=True, changed_path=()), SEEN)
    row = silver_of(merger).iloc[0]
    assert row["is_cancelled"]
    assert pd.isna(row["delay_min"])
    assert pd.isna(row["is_late"])
    # an emptied path (cancelled) keeps the planned destination
    assert row["final_destination"] == "Hamburg Hbf"


def test_changed_path_sets_destination_and_via() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    merger.add_change(change(changed_path=("Neumünster", "Elmshorn")), SEEN)
    (stop,) = merger.stops().values()
    assert final_destination(stop) == "Elmshorn"
    assert via(stop) == ("Neumünster", "Elmshorn")


def test_out_of_day_rows_excluded() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    merger.add_plan(
        event(
            stop_id="-6-2611130100-1",
            ride_id="-6-2611130100",
            stop_index=1,
            planned_utc=datetime(2026, 11, 13, 0, 5, tzinfo=UTC),
        ),
        SEEN,
    )
    assert len(silver_of(merger)) == 1


def test_fchg_only_stops_are_kept_apart() -> None:
    merger = StopMerger()
    merger.add_change(change(stop_id="-7-2611121400-2", ride_id="-7-2611121400"), SEEN)
    assert merger.stops() == {}
    assert list(merger.fchg_only()) == ["-7-2611121400-2"]


def test_unknown_station_and_missing_category_dropped() -> None:
    merger = StopMerger()
    merger.add_plan(event(eva="8000105"), SEEN)
    merger.add_plan(
        event(stop_id="-8-2611121400-1", ride_id="-8-2611121400", train_type=None), SEEN
    )
    frame, drops = to_silver(merger.stops(), STATIONS, DAY)
    assert frame.empty
    assert drops["not_supported_station"] == 1
    assert drops["no_train_type"] == 1


def test_output_passes_silver_contract() -> None:
    merger = StopMerger()
    merger.add_plan(event(), SEEN)
    merger.add_plan(event(stop_id="-9-2611121400-1", ride_id="-9-2611121400", stop_index=1), SEEN)
    merger.add_change(
        change(stop_id="-9-2611121400-1", ride_id="-9-2611121400", stop_index=1, cancelled=True),
        SEEN + timedelta(minutes=1),
    )
    frame = silver_of(merger)
    assert list(frame.columns) == list(SILVER_COLUMNS)
    validate_silver(frame)
    assert frame["ingested_at"].iloc[0] >= pd.Timestamp(SEEN)


def test_empty_input_gives_valid_empty_frame() -> None:
    frame, _ = to_silver({}, STATIONS, DAY)
    assert frame.empty
    validate_silver(frame)
