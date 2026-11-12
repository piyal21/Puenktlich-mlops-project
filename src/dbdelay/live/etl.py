"""Nightly ETL (Phase 7): live bronze → silver `source=live` for one UTC day + quality report.

Reads one bronze object at a time (memory stays small on 512 MB Lambdas): `plan` runs from the
day before to the day after (plans are fetched up to 6 h ahead and 3 h back), `fchg` runs from the
day and the day after (final delays of late-evening trains arrive after midnight). Overwrites the
day's partition and report, so a re-run is idempotent.
"""

from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import date, datetime, timedelta

from dbdelay.data.silver import SILVER_ARROW_SCHEMA, to_parquet_bytes
from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError
from dbdelay.live.conform import StopMerger, to_silver
from dbdelay.live.quality import LiveQualityReport, build_live_report
from dbdelay.storage import ObjectStore
from dbdelay.timetables.bronze import BronzeRecord, Kind, bronze_prefix, iter_records
from dbdelay.timetables.parser import StopEvent, parse_timetable

HTTP_OK = 200
HTTP_NOT_FOUND = 404


def live_silver_key(day: date) -> str:
    return f"silver/departures/source=live/date={day:%Y-%m-%d}/part-0.parquet"


def live_quality_key(day: date) -> str:
    return f"silver/_quality/source=live/date={day:%Y-%m-%d}.json"


def _records(store: ObjectStore, kind: Kind, days: Sequence[date]) -> Iterator[BronzeRecord]:
    keys = sorted(k for d in days for k in store.iter_keys(bronze_prefix(kind, d.isoformat())))
    for key in keys:
        yield from iter_records(store.get_bytes(key))


def _events(record: BronzeRecord) -> tuple[StopEvent, ...] | None:
    if record.status != HTTP_OK or record.body is None:
        return None
    try:
        return parse_timetable(record.body.encode("utf-8"), record.eva).events
    except DataValidationError:
        return None


def run_etl(store: ObjectStore, stations: Sequence[Station], day: date) -> LiveQualityReport:
    """Build, validate and write silver + quality report for UTC ``day``.

    Raises:
        DataValidationError: if a bronze object is unreadable or rows break the silver contract.
    """
    merger = StopMerger()
    plan_fetched: dict[str, set[str]] = defaultdict(set)
    fchg_ok: dict[str, list[datetime]] = defaultdict(list)
    before, after = day - timedelta(days=1), day + timedelta(days=1)

    for record in _records(store, "plan", [before, day, after]):
        events = _events(record)
        if record.hour is not None and (events is not None or record.status == HTTP_NOT_FOUND):
            plan_fetched[record.eva].add(record.hour)
        for event in events or ():
            merger.add_plan(event, record.requested_at)

    for record in _records(store, "fchg", [day, after]):
        events = _events(record)
        if events is None:
            continue
        fchg_ok[record.eva].append(record.requested_at)
        for event in events:
            merger.add_change(event, record.requested_at)

    stops = merger.stops()
    silver, drops = to_silver(stops, stations, day)
    report = build_live_report(
        day=day,
        silver=silver,
        stops=stops,
        fchg_ok=fchg_ok,
        plan_fetched=plan_fetched,
        fchg_only=len(merger.fchg_only()),
        stations=stations,
        drops=drops,
    )
    store.put_bytes(live_silver_key(day), to_parquet_bytes(silver, SILVER_ARROW_SCHEMA))
    store.put_bytes(
        live_quality_key(day), report.model_dump_json(indent=2).encode(), "application/json"
    )
    return report
