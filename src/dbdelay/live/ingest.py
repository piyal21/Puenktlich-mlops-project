"""One 15-minute ingest run (Phase 7): `fchg` for every station, plan top-up, bronze, board.

Partial success is fine (a failed station is recorded and counted); a run where no `fchg` call
succeeded raises after bronze is written, so the Lambda reports an error and the last good board
stays in place.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from dbdelay.data.stations import Station
from dbdelay.errors import DataValidationError, ExternalServiceError, NotFoundError
from dbdelay.live.board import build_live_board
from dbdelay.live.plans import add_hour, hour_key, load_cache, missing, prune, save_cache
from dbdelay.logging import get_logger
from dbdelay.serving.board import LiveBoard, board_from_bytes, board_to_bytes
from dbdelay.storage import ObjectStore
from dbdelay.timetables.bronze import BronzeRecord, Kind, bronze_key, records_to_bytes
from dbdelay.timetables.client import ApiResponse
from dbdelay.timetables.parser import StopEvent, parse_timetable

HTTP_OK = 200
HTTP_TOO_MANY = 429
# Worst case for one call: 3 attempts x 10 s timeout + backoff. Below this much time left, start no
# new call, so bronze, plan cache and board are always written before the Lambda timeout.
MARGIN_S = 45.0
HTTP_NOT_FOUND = 404  # plan for an hour without data: remember it as fetched (empty)


class TimetablesApi(Protocol):
    def fchg(self, eva: str) -> ApiResponse: ...
    def plan(self, eva: str, hour_local: datetime) -> ApiResponse: ...


@dataclass(frozen=True)
class IngestResult:
    calls: int
    fchg_ok: int
    failed_stations: tuple[str, ...]
    plan_hours_fetched: int
    events: int
    board_rows: int


def _call(
    kind: Kind, eva: str, hour: str | None, now: datetime, fetch: "_Fetch"
) -> tuple[BronzeRecord, ApiResponse | None, tuple[StopEvent, ...] | None]:
    """One API call → bronze record, the response, and its events (None unless 200 + valid)."""
    try:
        response = fetch()
    except ExternalServiceError as exc:
        return BronzeRecord(kind, eva, hour, now, None, None, type(exc).__name__), None, None
    body = response.body.decode("utf-8", errors="replace")
    if response.status != HTTP_OK:
        return BronzeRecord(kind, eva, hour, now, response.status, body, None), response, None
    try:
        events = parse_timetable(response.body, eva).events
    except DataValidationError as exc:
        record = BronzeRecord(kind, eva, hour, now, response.status, body, type(exc).__name__)
        return record, response, None
    return BronzeRecord(kind, eva, hour, now, response.status, body, None), response, events


class _Fetch(Protocol):
    def __call__(self) -> ApiResponse: ...


def _previous_board(store: ObjectStore, key: str) -> LiveBoard | None:
    """The last *live* board, or None: missing (S3 403 without s3:ListBucket), unreadable, or a
    sample board (at the switch to live it still sits at the key; never mix sample into live)."""
    try:
        board = board_from_bytes(store.get_bytes(key))
    except (NotFoundError, ExternalServiceError, DataValidationError):
        return None
    return board if board.source == "live" else None


def run_ingest(  # noqa: PLR0913 - keyword-only inputs of one run
    store: ObjectStore,
    client: TimetablesApi,
    stations: Sequence[Station],
    *,
    now: datetime,
    board_key: str,
    plan_budget: int,
    time_left: Callable[[], float] = lambda: math.inf,
) -> IngestResult:
    """Run once. Raises ``ExternalServiceError`` if no `fchg` call succeeded (bronze kept).

    ``time_left`` (seconds, e.g. from the Lambda context) stops new calls near the deadline; the
    plan top-up also stops after a 429 (the DB API is limiting us: don't spend more quota).
    """
    log = get_logger("ingest")
    fchg_records: list[BronzeRecord] = []
    changes: dict[str, tuple[StopEvent, ...]] = {}
    failed: list[str] = []
    for station in stations:
        eva = station.eva
        if time_left() < MARGIN_S:
            fchg_records.append(BronzeRecord("fchg", eva, None, now, None, None, "NoTimeLeft"))
            failed.append(eva)
            continue
        record, _, events = _call("fchg", eva, None, now, lambda e=eva: client.fchg(e))
        fchg_records.append(record)
        if events is None:
            failed.append(eva)
        else:
            changes[eva] = events

    cache = prune(load_cache(store), now)
    plan_records: list[BronzeRecord] = []
    fetched = 0
    # Every fchg call failed (DB API down, bad keys): plan calls would fail the same way.
    budget = plan_budget if changes else 0
    for eva, hour in missing(cache, [s.eva for s in stations], now, budget):
        if time_left() < MARGIN_S:
            break
        record, response, events = _call(
            "plan", eva, hour_key(hour), now, lambda e=eva, h=hour: client.plan(e, h)
        )
        plan_records.append(record)
        if response is not None and response.status == HTTP_TOO_MANY:
            break
        if events is None and response is not None and response.status == HTTP_NOT_FOUND:
            events = ()
        if events is not None:
            cache = add_hour(cache, eva, hour, events, now)
            fetched += 1

    bronze: tuple[tuple[Kind, list[BronzeRecord]], ...] = (
        ("fchg", fchg_records),
        ("plan", plan_records),
    )
    for kind, records in bronze:
        if records:
            store.put_bytes(bronze_key(kind, now), records_to_bytes(records), "application/gzip")
    save_cache(store, cache)

    calls = len(fchg_records) + len(plan_records)
    if not changes:
        log.error("no fchg call succeeded", extra={"calls": calls})
        raise ExternalServiceError(f"all {len(stations)} fchg calls failed")

    board = build_live_board(
        cache,
        changes,
        failed=frozenset(failed),
        previous=_previous_board(store, board_key),
        stations=stations,
        now=now,
        fetched_at=now,
    )
    store.put_bytes(board_key, board_to_bytes(board), "application/gzip")
    return IngestResult(
        calls=calls,
        fchg_ok=len(changes),
        failed_stations=tuple(failed),
        plan_hours_fetched=fetched,
        events=sum(len(e) for e in changes.values()),
        board_rows=len(board.departures),
    )
