import io
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from dbdelay.data.schemas import validate_silver
from dbdelay.data.stations import Station
from dbdelay.live.etl import live_quality_key, live_silver_key, run_etl
from dbdelay.live.quality import LiveQualityReport, day_hour_keys
from dbdelay.storage import ObjectStore
from dbdelay.timetables.bronze import BronzeRecord, bronze_key, records_to_bytes

DAY = date(2026, 11, 12)
KIEL = "8000199"
STATIONS = [Station(eva=KIEL, name="Kiel Hbf", state="SH")]


def plan_xml(*stops: tuple[str, str]) -> str:
    body = "".join(
        f'<s id="{sid}"><tl c="RE" n="7"/><dp pt="{pt}" pp="1" ppth="A|Hamburg Hbf"/></s>'
        for sid, pt in stops
    )
    return f'<timetable station="Kiel Hbf">{body}</timetable>'


def fchg_xml(*stops: tuple[str, str]) -> str:
    body = "".join(f'<s id="{sid}" eva="{KIEL}"><dp ct="{ct}"/></s>' for sid, ct in stops)
    return f'<timetable station="Kiel Hbf" eva="{KIEL}">{body}</timetable>'


def put(store: ObjectStore, kind: str, at: datetime, records: list[BronzeRecord]) -> None:
    store.put_bytes(bronze_key(kind, at), records_to_bytes(records))  # type: ignore[arg-type]


def plan_run(store: ObjectStore, at: datetime, hours: dict[str, str]) -> None:
    put(
        store,
        "plan",
        at,
        [BronzeRecord("plan", KIEL, h, at, 200, x, None) for h, x in hours.items()],
    )


def all_hours_empty(store: ObjectStore, at: datetime) -> None:
    plan_run(store, at, dict.fromkeys(day_hour_keys(DAY), plan_xml()))


def fchg_runs(store: ObjectStore, start: datetime, end: datetime, xml: str) -> None:
    at = start
    while at <= end:
        put(store, "fchg", at, [BronzeRecord("fchg", KIEL, None, at, 200, xml, None)])
        at += timedelta(minutes=15)


def silver(store: ObjectStore, day: date = DAY) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(store.get_bytes(live_silver_key(day))))


START = datetime(2026, 11, 12, tzinfo=UTC)
STOP_A = "-1-2611121000-2"


def test_keys() -> None:
    assert live_silver_key(DAY) == "silver/departures/source=live/date=2026-11-12/part-0.parquet"
    assert live_quality_key(DAY) == "silver/_quality/source=live/date=2026-11-12.json"


def test_etl_writes_valid_silver_and_report(s3_store: ObjectStore) -> None:
    all_hours_empty(s3_store, START + timedelta(hours=1))
    plan_run(s3_store, START + timedelta(hours=8), {"26111211": plan_xml((STOP_A, "2611121105"))})
    fchg_runs(s3_store, START, START + timedelta(hours=27), fchg_xml((STOP_A, "2611121112")))
    report = run_etl(s3_store, STATIONS, DAY)
    frame = silver(s3_store)
    validate_silver(frame)
    assert frame["delay_min"].tolist() == [7]
    assert frame["is_late"].tolist() == [True]
    assert frame["source"].tolist() == ["live"]
    stored = LiveQualityReport.model_validate_json(s3_store.get_bytes(live_quality_key(DAY)))
    assert stored == report
    assert report.rows_out == 1
    assert report.fchg_runs_ok == {KIEL: 96}
    assert report.stations[0].volume_drop_days == []


def test_etl_is_idempotent(s3_store: ObjectStore) -> None:
    all_hours_empty(s3_store, START + timedelta(hours=1))
    plan_run(s3_store, START + timedelta(hours=8), {"26111211": plan_xml((STOP_A, "2611121105"))})
    fchg_runs(s3_store, START, START + timedelta(hours=27), fchg_xml((STOP_A, "2611121112")))
    run_etl(s3_store, STATIONS, DAY)
    first = (s3_store.get_bytes(live_silver_key(DAY)), s3_store.get_bytes(live_quality_key(DAY)))
    run_etl(s3_store, STATIONS, DAY)
    assert (
        s3_store.get_bytes(live_silver_key(DAY)),
        s3_store.get_bytes(live_quality_key(DAY)),
    ) == first


def test_departures_at_the_utc_day_edges(s3_store: ObjectStore) -> None:
    all_hours_empty(s3_store, START + timedelta(hours=1))
    late_night = "-2-2611122300-5"  # 00:50 Berlin on Nov 13 = 23:50 UTC on Nov 12
    early = "-3-2611120100-1"  # 01:30 Berlin = 00:30 UTC on Nov 12, planned the day before
    plan_run(
        s3_store,
        datetime(2026, 11, 11, 19, tzinfo=UTC),
        {"26111201": plan_xml((early, "2611120130"))},
    )
    plan_run(
        s3_store, START + timedelta(hours=18), {"26111300": plan_xml((late_night, "2611130050"))}
    )
    fchg_runs(s3_store, START, START + timedelta(hours=23), fchg_xml())
    put(
        s3_store,
        "fchg",
        datetime(2026, 11, 13, 0, 0, tzinfo=UTC),
        [BronzeRecord("fchg", KIEL, None, datetime(2026, 11, 13, tzinfo=UTC), 200,
                      fchg_xml((late_night, "2611130058")), None)],
    )  # fmt: skip
    run_etl(s3_store, STATIONS, DAY)
    frame = silver(s3_store).set_index("ride_id")
    assert frame.loc["-2-2611122300", "delay_min"] == 8
    assert frame.loc["-2-2611122300", "planned_departure_utc"] == pd.Timestamp("2026-11-12T23:50Z")
    assert frame.loc["-3-2611120100", "delay_min"] == 0


def test_failed_calls_do_not_count_and_404_plan_counts_as_fetched(s3_store: ObjectStore) -> None:
    keys = day_hour_keys(DAY)
    at = START + timedelta(hours=1)
    put(s3_store, "plan", at, [BronzeRecord("plan", KIEL, h, at, 404, "", None) for h in keys])
    put(s3_store, "fchg", at, [BronzeRecord("fchg", KIEL, None, at, 503, "", None)])
    report = run_etl(s3_store, STATIONS, DAY)
    assert report.plan_hours_missing == {}
    assert report.fchg_runs_ok == {KIEL: 0}
    assert report.empty_stations == [KIEL]
    assert silver(s3_store).empty


def test_plan_404_hour_with_known_trains_is_a_gap(s3_store: ObjectStore) -> None:
    # A 404 for an hour in which fchg shows a planned train means the plan was lost, not empty:
    # only changed trains would survive, inflating the late rate.
    keys = day_hour_keys(DAY)
    at = START + timedelta(hours=1)
    records = [
        BronzeRecord("plan", KIEL, h, at, 404 if h == "26111211" else 200, plan_xml(), None)
        for h in keys
    ]
    put(s3_store, "plan", at, records)
    added = (
        f'<s id="{STOP_A}" eva="{KIEL}"><tl c="RE" n="7"/>'
        '<dp pt="2611121105" ct="2611121125" ppth="A|B"/></s>'
    )
    fchg_runs(
        s3_store,
        START,
        START + timedelta(hours=27),
        f'<timetable station="Kiel Hbf">{added}</timetable>',
    )
    report = run_etl(s3_store, STATIONS, DAY)
    assert report.plan_hours_missing == {KIEL: ["26111211"]}
    assert report.stations[0].volume_drop_days == ["2026-11-12"]


def test_parser_drops_reach_the_report(s3_store: ObjectStore) -> None:
    all_hours_empty(s3_store, START + timedelta(hours=1))
    bad = '<timetable><s id="-4-2611120000-1"><tl c="RE" n="1"/><dp pt="26xx"/></s></timetable>'
    plan_run(s3_store, START + timedelta(hours=2), {"26111211": bad})
    report = run_etl(s3_store, STATIONS, DAY)
    assert report.drops["parse_bad_time"] == 1
