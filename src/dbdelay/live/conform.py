"""Live observations → silver rows (architecture §3.3), shared by ETL, parity and reconciliation.

A stop's planned facts come from its latest `plan` observation, its changes from its latest
`fchg` observation (latest ``seen_at`` wins, so a reinstated train is not cancelled). No change
reported ⇒ changed = planned, delay 0 (ADR 0001) — the quality report flags where that is unsafe.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from dbdelay.data.schemas import LATE_THRESHOLD_MIN, validate_silver
from dbdelay.data.silver import SILVER_COLUMNS, make_event_id
from dbdelay.data.stations import Station
from dbdelay.timetables.parser import StopEvent

DROP_REASONS = ("no_planned_time", "no_train_type", "not_supported_station", "duplicate")


@dataclass
class MergedStop:
    plan: StopEvent
    plan_seen_at: datetime
    change: StopEvent | None = None
    change_seen_at: datetime | None = None


class StopMerger:
    """Keeps the latest plan and the latest change observation per `s@id`."""

    def __init__(self) -> None:
        self._plans: dict[str, tuple[StopEvent, datetime]] = {}
        self._changes: dict[str, tuple[StopEvent, datetime]] = {}

    @staticmethod
    def _keep_latest(
        into: dict[str, tuple[StopEvent, datetime]], event: StopEvent, seen_at: datetime
    ) -> None:
        current = into.get(event.stop_id)
        if current is None or seen_at >= current[1]:
            into[event.stop_id] = (event, seen_at)

    def add_plan(self, event: StopEvent, seen_at: datetime) -> None:
        self._keep_latest(self._plans, event, seen_at)

    def add_change(self, event: StopEvent, seen_at: datetime) -> None:
        self._keep_latest(self._changes, event, seen_at)

    def stops(self) -> dict[str, MergedStop]:
        """Stops with a plan observation, each with its latest change (if any)."""
        merged = {}
        for stop_id, (plan, plan_seen) in self._plans.items():
            change, change_seen = self._changes.get(stop_id, (None, None))
            merged[stop_id] = MergedStop(plan, plan_seen, change, change_seen)
        return merged

    def fchg_only(self) -> dict[str, StopEvent]:
        """Changes for stops never seen in a plan (e.g. added trains)."""
        return {k: e for k, (e, _) in self._changes.items() if k not in self._plans}


def _path(stop: MergedStop) -> tuple[str, ...]:
    """The changed path when one is reported and not empty (cancelled), else the planned one."""
    if stop.change is not None and stop.change.changed_path:
        return stop.change.changed_path
    return stop.plan.planned_path


def final_destination(stop: MergedStop) -> str | None:
    path = _path(stop)
    return path[-1] if path else None


def via(stop: MergedStop) -> tuple[str, ...]:
    return _path(stop)


def _empty_silver() -> pd.DataFrame:
    utc = "datetime64[us, UTC]"
    return pd.DataFrame(
        {
            "event_id": pd.Series(dtype="object"),
            "eva": pd.Series(dtype="object"),
            "station_name": pd.Series(dtype="object"),
            "ride_id": pd.Series(dtype="object"),
            "stop_index": pd.Series(dtype="int16"),
            "train_type": pd.Series(dtype="object"),
            "train_number": pd.Series(dtype="object"),
            "line_number": pd.Series(dtype="object"),
            "final_destination": pd.Series(dtype="object"),
            "planned_departure_utc": pd.Series(dtype=utc),
            "changed_departure_utc": pd.Series(dtype=utc),
            "delay_min": pd.Series(dtype="Int16"),
            "is_cancelled": pd.Series(dtype="bool"),
            "is_late": pd.Series(dtype="boolean"),
            "source": pd.Series(dtype="object"),
            "ingested_at": pd.Series(dtype=utc),
        }
    )


def _row(stop: MergedStop, names: Mapping[str, str]) -> dict[str, object]:
    plan, change = stop.plan, stop.change
    planned = plan.planned_utc
    assert planned is not None  # noqa: S101 - filtered by the caller
    cancelled = change.cancelled if change is not None else plan.cancelled
    changed = (change.changed_utc if change is not None else None) or planned
    delay = None if cancelled else round((changed - planned).total_seconds() / 60)
    seen = [t for t in (stop.plan_seen_at, stop.change_seen_at) if t is not None]
    return {
        "event_id": make_event_id(plan.eva, plan.ride_id, pd.Timestamp(planned)),
        "eva": plan.eva,
        "station_name": names[plan.eva],
        "ride_id": plan.ride_id,
        "stop_index": plan.stop_index,
        "train_type": plan.train_type,
        "train_number": plan.train_number,
        "line_number": plan.line_number,
        "final_destination": final_destination(stop),
        "planned_departure_utc": planned,
        "changed_departure_utc": changed,
        "delay_min": delay,
        "is_cancelled": cancelled,
        "is_late": None if delay is None else delay >= LATE_THRESHOLD_MIN,
        "source": "live",
        "ingested_at": max(seen),
    }


def to_silver(
    stops: Mapping[str, MergedStop], stations: Sequence[Station], day: date
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Silver rows whose planned departure falls on UTC ``day``, validated, sorted by event_id.

    Raises:
        DataValidationError: if the rows break the silver contract.
    """
    names = {s.eva: s.name for s in stations}
    drops = dict.fromkeys(DROP_REASONS, 0)
    rows = []
    for stop in stops.values():
        plan = stop.plan
        if plan.planned_utc is None:
            drops["no_planned_time"] += 1
            continue
        if plan.planned_utc.date() != day:
            continue
        if plan.eva not in names:
            drops["not_supported_station"] += 1
            continue
        if plan.train_type is None:
            drops["no_train_type"] += 1
            continue
        rows.append(_row(stop, names))
    if not rows:
        return _empty_silver(), drops

    frame = pd.DataFrame(rows)
    for column in ("planned_departure_utc", "changed_departure_utc", "ingested_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True).dt.as_unit("us")
    frame["stop_index"] = frame["stop_index"].astype("int16")
    frame["delay_min"] = frame["delay_min"].astype("Int16")
    frame["is_late"] = frame["is_late"].astype("boolean")
    frame["is_cancelled"] = frame["is_cancelled"].astype(bool)
    for column in ("train_number", "line_number", "final_destination"):
        values = frame[column].astype("object")
        values[frame[column].isna()] = None  # the contract rejects NaN floats
        frame[column] = values

    frame = frame.sort_values(["event_id", "ingested_at"], ascending=[True, False], kind="stable")
    duplicate = frame["event_id"].duplicated()
    drops["duplicate"] = int(duplicate.sum())
    frame = frame[~duplicate][list(SILVER_COLUMNS)].reset_index(drop=True)
    validate_silver(frame)
    return frame, drops
