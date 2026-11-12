"""Live board (`source="live"`): plan cache + this run's `fchg` → `LiveBoard` for the API.

Same outcome rules as live silver (`conform.stop_outcome`). A station whose `fchg` call failed
keeps its rows from the previous board, so one bad call never empties a station.
"""

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timedelta

from pandas import Timestamp

from dbdelay.data.silver import make_event_id
from dbdelay.data.stations import Station
from dbdelay.live.conform import StopMerger, final_destination, stop_outcome, via
from dbdelay.live.plans import PlanCache
from dbdelay.serving.board import BoardDeparture, LiveBoard
from dbdelay.timetables.parser import StopEvent

BEFORE = timedelta(minutes=30)
AFTER = timedelta(hours=6)


def _in_window(dep: BoardDeparture, now: datetime) -> bool:
    return dep.leaves_at >= now - BEFORE and dep.planned_departure_utc <= now + AFTER


def build_live_board(  # noqa: PLR0913 - keyword-only inputs of one run
    cache: PlanCache,
    changes: Mapping[str, Sequence[StopEvent]],
    *,
    failed: Collection[str],
    previous: LiveBoard | None,
    stations: Sequence[Station],
    now: datetime,
    fetched_at: datetime,
) -> LiveBoard:
    names = {s.eva: s.name for s in stations}
    merger = StopMerger()
    for event in cache.stops:
        merger.add_plan(event, cache.seen_at.get(event.stop_id, fetched_at))
    for events in changes.values():
        for event in events:
            merger.add_change(event, fetched_at)

    departures: list[BoardDeparture] = []
    for stop in merger.stops().values():
        plan, change = stop.plan, stop.change
        outcome = stop_outcome(stop)
        if (
            outcome is None
            or plan.eva in failed
            or plan.eva not in names
            or plan.train_type is None
        ):
            continue
        dep = BoardDeparture(
            event_id=make_event_id(plan.eva, plan.ride_id, Timestamp(outcome.planned)),
            eva=plan.eva,
            station_name=names[plan.eva],
            ride_id=plan.ride_id,
            stop_index=plan.stop_index,
            train_type=plan.train_type,
            train_number=plan.train_number,
            line_number=plan.line_number,
            final_destination=final_destination(stop),
            planned_departure_utc=outcome.planned,
            changed_departure_utc=outcome.changed,
            delay_min=outcome.delay_min,
            is_cancelled=outcome.cancelled,
            platform=(change.changed_platform if change else None) or plan.planned_platform,
            via=via(stop),
        )
        if _in_window(dep, now):
            departures.append(dep)

    if previous is not None:
        departures.extend(d for d in previous.departures if d.eva in failed and _in_window(d, now))
    departures.sort(key=lambda d: (d.planned_departure_utc, d.event_id))
    return LiveBoard(generated_at=fetched_at, source="live", departures=tuple(departures))
