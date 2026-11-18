from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from dbdelay.data.stations import Station
from dbdelay.live.conform import StopMerger, to_silver
from dbdelay.live.quality import LiveQualityReport, build_live_report, day_hour_keys
from dbdelay.timetables.parser import StopEvent
from dbdelay.training.split import gap_masks

DAY = date(2026, 11, 12)
START = datetime(2026, 11, 12, tzinfo=UTC)
KIEL, FFM, MUC = "8000199", "8000105", "8000261"
STATIONS = [
    Station(eva=KIEL, name="Kiel Hbf", state="SH"),
    Station(eva=FFM, name="Frankfurt(Main)Hbf", state="HE"),
    Station(eva=MUC, name="München Hbf", state="BY"),
]


def event(n: int, eva: str, planned: datetime) -> StopEvent:
    fields: dict[str, Any] = {
        "stop_id": f"-{n}-2611120000-1",
        "ride_id": f"-{n}-2611120000",
        "stop_index": 1,
        "eva": eva,
        "train_type": "RE",
        "train_number": str(n),
        "line_number": None,
        "planned_utc": planned,
        "changed_utc": None,
        "cancelled": False,
        "planned_platform": None,
        "changed_platform": None,
        "planned_path": ("X",),
        "changed_path": None,
        "distant_endpoint": None,
    }
    return StopEvent(**fields)


def every_15_min(start: datetime, end: datetime) -> list[datetime]:
    n = int((end - start).total_seconds() // 900)
    return [start + timedelta(minutes=15 * i) for i in range(n + 1)]


FULL = every_15_min(START, START + timedelta(hours=27))
ALL_HOURS = set(day_hour_keys(DAY))


def report(
    events: list[StopEvent], fchg_ok: dict[str, list[datetime]], plan_fetched: dict[str, set[str]]
) -> LiveQualityReport:
    merger = StopMerger()
    for e in events:
        merger.add_plan(e, START)
    silver, drops = to_silver(merger.stops(), STATIONS, DAY)
    return build_live_report(
        day=DAY,
        silver=silver,
        stops=merger.stops(),
        fchg_ok=fchg_ok,
        plan_fetched=plan_fetched,
        fchg_only=0,
        stations=STATIONS,
        drops=drops,
    )


def test_day_hour_keys_cover_the_utc_day_in_berlin_hours() -> None:
    keys = day_hour_keys(DAY)
    assert keys[0] == "26111201" and keys[-1] == "26111300"  # CET = UTC+1
    assert len(keys) == 24


def test_full_coverage_has_no_flags() -> None:
    events = [
        event(1, KIEL, START + timedelta(hours=10)),
        event(2, FFM, START + timedelta(hours=10)),
    ]
    fetched = {KIEL: ALL_HOURS, FFM: ALL_HOURS, MUC: ALL_HOURS}
    r = report(events, {KIEL: FULL, FFM: FULL, MUC: FULL}, fetched)
    assert r.source == "live"
    assert r.rows_out == 2
    assert r.low_volume_hours == []
    assert all(s.volume_drop_days == [] for s in r.stations)
    assert r.fchg_runs_ok[KIEL] == 96
    assert r.longest_fchg_gap_min[KIEL] == 15
    assert r.never_seen_in_fchg == 2
    assert r.empty_stations == [MUC]


def test_missing_fchg_runs_flag_station_day() -> None:
    events = [
        event(1, KIEL, START + timedelta(hours=10)),
        event(2, FFM, START + timedelta(hours=10)),
    ]
    kiel_runs = every_15_min(START, START + timedelta(hours=8))  # stops at 08:00 UTC
    fetched = {KIEL: ALL_HOURS, FFM: ALL_HOURS, MUC: ALL_HOURS}
    r = report(events, {KIEL: kiel_runs, FFM: FULL, MUC: FULL}, fetched)
    by_eva = {s.eva: s.volume_drop_days for s in r.stations}
    assert by_eva[KIEL] == ["2026-11-12"]
    assert by_eva[FFM] == []
    assert r.low_volume_hours == []  # Frankfurt was covered in that hour
    assert r.longest_fchg_gap_min[KIEL] == 16 * 60


def test_all_stations_uncovered_flags_hour() -> None:
    events = [
        event(1, KIEL, START + timedelta(hours=10)),
        event(2, FFM, START + timedelta(hours=10, minutes=30)),
    ]
    runs = [t for t in FULL if not START + timedelta(hours=9) < t < START + timedelta(hours=14)]
    fetched = {KIEL: ALL_HOURS, FFM: ALL_HOURS, MUC: ALL_HOURS}
    r = report(events, {KIEL: runs, FFM: runs, MUC: runs}, fetched)
    assert r.low_volume_hours == ["2026-11-12T11"]  # 10:00 UTC = 11:00 Berlin


def test_missing_plan_hour_flags_station_day() -> None:
    fetched = {KIEL: ALL_HOURS - {"26111215"}, FFM: ALL_HOURS, MUC: ALL_HOURS}
    r = report([], {KIEL: FULL, FFM: FULL, MUC: FULL}, fetched)
    assert r.plan_hours_missing == {KIEL: ["26111215"]}
    assert {s.eva: s.volume_drop_days for s in r.stations}[KIEL] == ["2026-11-12"]


def test_gap_masks_accepts_live_report() -> None:
    events = [
        event(1, KIEL, START + timedelta(hours=10)),
        event(2, FFM, START + timedelta(hours=10)),
    ]
    kiel_runs = every_15_min(START, START + timedelta(hours=8))
    fetched = {KIEL: ALL_HOURS, FFM: ALL_HOURS, MUC: ALL_HOURS}
    r = report(events, {KIEL: kiel_runs, FFM: FULL, MUC: FULL}, fetched)
    frame = pd.DataFrame(
        {
            "eva": [KIEL, FFM],
            "planned_departure_utc": pd.to_datetime([START + timedelta(hours=10)] * 2, utc=True),
        }
    )
    in_hour, in_day = gap_masks(frame, [r])
    assert in_hour.tolist() == [False, False]
    assert in_day.tolist() == [True, False]
