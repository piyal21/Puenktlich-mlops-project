"""Live observations → silver rows (architecture §3.3), shared by ETL, parity and reconciliation.

A stop's planned facts come from its latest `plan` observation, its changes from its latest
`fchg` observation (latest ``seen_at`` wins, so a reinstated train is not cancelled). No change
reported ⇒ changed = planned, delay 0 (ADR 0001) — the quality report flags where that is unsafe.

Rules matched to HF's processing by the parity check on 2026-08-24 (Phase 7):
- a stop seen only in `fchg` counts when an `fchg` observation carries its planned time and train
  category (HF keeps these; mostly long-distance trains);
- ``final_destination`` is the last *planned* stop, even when `cpth` shortens the route.
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
        # Latest `fchg` observation that carries planned facts (stands in for a missing plan).
        self._plan_like: dict[str, tuple[StopEvent, datetime]] = {}

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
        if event.planned_utc is not None and event.train_type is not None:
            self._keep_latest(self._plan_like, event, seen_at)

    def stops(self) -> dict[str, MergedStop]:
        """Stops with planned facts (from `plan`, else from `fchg`), each with its latest change."""
        merged = {}
        for stop_id, (plan, plan_seen) in (self._plan_like | self._plans).items():
            change, change_seen = self._changes.get(stop_id, (None, None))
            merged[stop_id] = MergedStop(plan, plan_seen, change, change_seen)
        return merged

    def fchg_only(self) -> dict[str, StopEvent]:
        """Changes for stops without any planned facts (not in silver, counted in the report)."""
        known = self._plans.keys() | self._plan_like.keys()
        return {k: e for k, (e, _) in self._changes.items() if k not in known}


@dataclass(frozen=True)
class StopOutcome:
    planned: datetime
    changed: datetime  # ``planned`` when no change was reported (ADR 0001)
    delay_min: int | None  # None when cancelled
    cancelled: bool


def stop_outcome(stop: MergedStop) -> StopOutcome | None:
    """What happened to a stop per its latest observations; ``None`` without a planned time."""
    plan, change = stop.plan, stop.change
    planned = plan.planned_utc
    if planned is None:
        return None
    cancelled = change.cancelled if change is not None else plan.cancelled
    changed = (change.changed_utc if change is not None else None) or planned
    delay = None if cancelled else round((changed - planned).total_seconds() / 60)
    return StopOutcome(planned, changed, delay, cancelled)


def final_destination(stop: MergedStop) -> str | None:
    """Last planned stop (HF rule; also what the model was trained on)."""
    path = stop.plan.planned_path
    return path[-1] if path else None


def via(stop: MergedStop) -> tuple[str, ...]:
    """Stops ahead for the board: the changed path when reported and not empty, else planned."""
    if stop.change is not None and stop.change.changed_path:
        return stop.change.changed_path
    return stop.plan.planned_path


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


def _row(stop: MergedStop, outcome: StopOutcome, names: Mapping[str, str]) -> dict[str, object]:
    plan = stop.plan
    seen = [t for t in (stop.plan_seen_at, stop.change_seen_at) if t is not None]
    delay = outcome.delay_min
    return {
        "event_id": make_event_id(plan.eva, plan.ride_id, pd.Timestamp(outcome.planned)),
        "eva": plan.eva,
        "station_name": names[plan.eva],
        "ride_id": plan.ride_id,
        "stop_index": plan.stop_index,
        "train_type": plan.train_type,
        "train_number": plan.train_number,
        "line_number": plan.line_number,
        "final_destination": final_destination(stop),
        "planned_departure_utc": outcome.planned,
        "changed_departure_utc": outcome.changed,
        "delay_min": delay,
        "is_cancelled": outcome.cancelled,
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
        outcome = stop_outcome(stop)
        if outcome is None:
            drops["no_planned_time"] += 1
            continue
        if outcome.planned.date() != day:
            continue
        if plan.eva not in names:
            drops["not_supported_station"] += 1
            continue
        if plan.train_type is None:
            drops["no_train_type"] += 1
            continue
        rows.append(_row(stop, outcome, names))
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
