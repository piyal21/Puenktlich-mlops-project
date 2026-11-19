"""Live silver keys and the daily live quality report model (Phase 7 contract).

Kept free of the jobs-image packages (defusedxml, httpx) so the monitor on the API image can read
live silver and its gap flags (Phase 9).
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from dbdelay.data.quality import StationQuality
from dbdelay.data.schemas import SILVER_SCHEMA_VERSION

RUNS_PER_DAY = 96


def live_silver_key(day: date) -> str:
    return f"silver/departures/source=live/date={day:%Y-%m-%d}/part-0.parquet"


def live_quality_key(day: date) -> str:
    return f"silver/_quality/source=live/date={day:%Y-%m-%d}.json"


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
