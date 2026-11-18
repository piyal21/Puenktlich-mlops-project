"""Daily quality report for live silver, with the gap fields training already reads.

`low_volume_hours` (Berlin "%Y-%m-%dT%H") and `stations[].volume_drop_days` (UTC days) have the
same meaning for `data.gaps.gap_masks` as in the HF monthly reports. Live rule (Phase 7):
a departure is *covered* when its station had a successful `fchg` call within 3 h after the planned
time; otherwise "no change reported ⇒ on time" may be wrong (risk #1).
- station-day flagged: any uncovered departure, or any Berlin hour of the day without a plan;
- hour flagged: every departure in that Berlin hour is uncovered (an outage for all stations).
"""

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from dbdelay.data.quality import StationQuality, content_hash
from dbdelay.data.schemas import SILVER_SCHEMA_VERSION
from dbdelay.data.stations import Station
from dbdelay.live.conform import MergedStop
from dbdelay.live.plans import hour_key
from dbdelay.timetables.parser import BERLIN

COVER_WINDOW = timedelta(hours=3)
RUNS_PER_DAY = 96


class LiveQualityReport(BaseModel):
    schema_version: int = SILVER_SCHEMA_VERSION
    source: Literal["live"] = "live"
    day: date
    rows_out: int
    drops: dict[str, int]
    late_rate: float | None
    cancelled_rate: float | None
    stations: list[StationQuality]
    low_volume_hours: list[str]
    fchg_runs_expected: int = RUNS_PER_DAY
    fchg_runs_ok: dict[str, int]
    longest_fchg_gap_min: dict[str, int]
    plan_hours_missing: dict[str, list[str]]
    never_seen_in_fchg: int
    fchg_only_stops: int
    empty_stations: list[str]
    content_hash: str


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def day_hour_keys(day: date) -> list[str]:
    """Berlin ``YYMMDDHH`` keys of the hours that overlap UTC ``day`` (each once)."""
    start, _ = day_bounds(day)
    keys: list[str] = []
    for offset in range(24):
        key = hour_key(start + timedelta(hours=offset))
        if key not in keys:
            keys.append(key)
    return keys


def _covered(planned: datetime, runs: Sequence[datetime]) -> bool:
    i = bisect_left(runs, planned)
    return i < len(runs) and runs[i] <= planned + COVER_WINDOW


def _longest_gap_min(runs: Sequence[datetime], start: datetime, end: datetime) -> int:
    points = [start, *(t for t in runs if start <= t <= end), end]
    return int(max(b - a for a, b in pairwise(points)).total_seconds() // 60)


def build_live_report(  # noqa: PLR0913 - keyword-only facts of one day
    *,
    day: date,
    silver: pd.DataFrame,
    stops: Mapping[str, MergedStop],
    fchg_ok: Mapping[str, Sequence[datetime]],
    plan_fetched: Mapping[str, set[str]],
    fchg_only: int,
    stations: Sequence[Station],
    drops: Mapping[str, int],
) -> LiveQualityReport:
    start, end = day_bounds(day)
    runs = {s.eva: sorted(fchg_ok.get(s.eva, [])) for s in stations}

    covered = [
        _covered(planned.to_pydatetime(), runs.get(eva, []))
        for eva, planned in zip(silver["eva"], silver["planned_departure_utc"], strict=True)
    ]
    rows = silver.assign(_covered=covered)
    plan_missing = {
        s.eva: missing
        for s in stations
        if (missing := [k for k in day_hour_keys(day) if k not in plan_fetched.get(s.eva, set())])
    }
    flagged_days = set(rows.loc[~rows["_covered"], "eva"]) | set(plan_missing)

    low_hours: list[str] = []
    if not rows.empty:
        local_hour = rows["planned_departure_utc"].dt.floor("h").dt.tz_convert(BERLIN)
        by_hour = rows["_covered"].groupby(local_hour).any()
        low_hours = [f"{ts:%Y-%m-%dT%H}" for ts, any_covered in by_hour.items() if not any_covered]

    counts = rows["eva"].value_counts()
    labelled = rows["is_late"].dropna()
    unseen = sum(
        1
        for s in stops.values()
        if s.change is None and s.plan.planned_utc is not None and start <= s.plan.planned_utc < end
    )
    return LiveQualityReport(
        day=day,
        rows_out=len(rows),
        drops=dict(drops),
        late_rate=float(labelled.astype(bool).mean()) if len(labelled) else None,
        cancelled_rate=float(rows["is_cancelled"].mean()) if len(rows) else None,
        stations=[
            StationQuality(
                eva=s.eva,
                rows=int(counts.get(s.eva, 0)),
                volume_drop_days=[day.isoformat()] if s.eva in flagged_days else [],
            )
            for s in stations
        ],
        low_volume_hours=low_hours,
        fchg_runs_ok={eva: sum(start <= t < end for t in r) for eva, r in runs.items()},
        longest_fchg_gap_min={eva: _longest_gap_min(r, start, end) for eva, r in runs.items()},
        plan_hours_missing=plan_missing,
        never_seen_in_fchg=unseen,
        fchg_only_stops=fchg_only,
        empty_stations=[s.eva for s in stations if counts.get(s.eva, 0) == 0],
        content_hash=content_hash(silver),
    )
