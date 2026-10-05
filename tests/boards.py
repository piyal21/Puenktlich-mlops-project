"""Live-board builders for serving/API tests."""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import pandas as pd

from dbdelay.data.silver import make_event_id
from dbdelay.serving.board import BoardDeparture, LiveBoard

NOW = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)  # Monday 15:00 in Berlin (CEST)
FRANKFURT = "8000105"
MUENCHEN = "8000261"


def departure(minutes: int, **overrides: Any) -> BoardDeparture:
    """A Frankfurt ICE departing ``minutes`` after ``NOW`` (on time unless overridden)."""
    planned: datetime = overrides.pop("planned_departure_utc", NOW + timedelta(minutes=minutes))
    eva: str = overrides.get("eva", FRANKFURT)
    ride: str = overrides.pop("ride_id", f"r{minutes + 1000}-2610050000")
    row: dict[str, Any] = {
        "event_id": make_event_id(eva, ride, pd.Timestamp(planned)),
        "eva": eva,
        "station_name": "Frankfurt (Main) Hbf",
        "ride_id": ride,
        "stop_index": 3,
        "train_type": "ICE",
        "train_number": "1602",
        "line_number": None,
        "final_destination": "Berlin Hbf",
        "planned_departure_utc": planned,
        "changed_departure_utc": planned,
        "delay_min": 0,
        "is_cancelled": False,
        "platform": "7",
    }
    row.update(overrides)
    return BoardDeparture(**row)


def live_board(
    *departures: BoardDeparture,
    generated_at: datetime = NOW,
    source: Literal["sample", "live"] = "sample",
) -> LiveBoard:
    return LiveBoard(
        generated_at=generated_at,
        source=source,
        replayed_from=date(2026, 8, 24) if source == "sample" else None,
        departures=departures,
    )
