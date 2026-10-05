"""Sample live board for local serving (Phase 5): a real silver day replayed onto today.

Boards are labelled ``source="sample"`` with ``replayed_from=<day>``; the UI says so. Delay and
cancellation values are the real ones of that day. Phase 7 ingestion writes live boards instead.
"""

import argparse
import io
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from dbdelay.config import get_settings
from dbdelay.data.silver import make_event_id, silver_key
from dbdelay.errors import DataValidationError, DbDelayError, NotFoundError
from dbdelay.features.calendar import BERLIN
from dbdelay.serving.board import BOARD_KEY, BoardDeparture, LiveBoard, board_to_bytes
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.split import latest_silver_day

BEFORE = timedelta(minutes=30)
AFTER = timedelta(hours=6)


def _berlin_today(now: datetime) -> date:
    return now.astimezone(ZoneInfo(BERLIN)).date()


def pick_source_day(latest: date, today: date) -> date:
    """Newest day before ``latest`` (so its next day exists too) with ``today``'s weekday."""
    day = latest - timedelta(days=1)
    while day.weekday() != today.weekday():
        day -= timedelta(days=1)
    return day


def read_source_rows(store: ObjectStore, source_day: date, root: str = "") -> pd.DataFrame:
    """Silver rows planned on ``source_day`` or the day after (Berlin dates).

    Raises:
        DataValidationError: if none of the surrounding UTC partitions exist.
    """
    frames = []
    for offset in range(-1, 3):
        try:
            data = store.get_bytes(silver_key(source_day + timedelta(days=offset), root))
        except NotFoundError:
            continue
        frames.append(pd.read_parquet(io.BytesIO(data)))
    if not frames:
        raise DataValidationError(f"no silver partitions around {source_day}")
    rows = pd.concat(frames, ignore_index=True)
    local_day = rows["planned_departure_utc"].dt.tz_convert(BERLIN).dt.date
    keep = local_day.isin({source_day, source_day + timedelta(days=1)})
    selected: pd.DataFrame = rows.loc[keep.to_numpy()].reset_index(drop=True)
    return selected


def _optional(value: object) -> str | None:
    return None if pd.isna(value) else str(value)  # type: ignore[call-overload]


def replay_board(rows: pd.DataFrame, *, source_day: date, now: datetime) -> LiveBoard:
    """Move ``rows`` from ``source_day`` onto today keeping Berlin wall-clock times (a calendar
    shift, so DST is handled), recompute ``event_id``, keep ``[now - 30 min, now + 6 h]``."""
    days = (_berlin_today(now) - source_day).days
    local = rows["planned_departure_utc"].dt.tz_convert(BERLIN).dt.tz_localize(None)
    planned = (
        (local + np.timedelta64(days, "D"))
        .dt.tz_localize(BERLIN, ambiguous="NaT", nonexistent="NaT")
        .dt.tz_convert("UTC")
    )
    changed = planned + (rows["changed_departure_utc"] - rows["planned_departure_utc"])
    window = planned.notna() & (planned >= now - BEFORE) & (planned <= now + AFTER)
    departures = []
    for i in np.flatnonzero(window.to_numpy()):
        row = rows.iloc[i]
        when = planned.iloc[i]
        moved = changed.iloc[i]
        departures.append(
            BoardDeparture(
                event_id=make_event_id(str(row["eva"]), str(row["ride_id"]), when),
                eva=str(row["eva"]),
                station_name=str(row["station_name"]),
                ride_id=str(row["ride_id"]),
                stop_index=int(row["stop_index"]),
                train_type=str(row["train_type"]),
                train_number=_optional(row["train_number"]),
                line_number=_optional(row["line_number"]),
                final_destination=_optional(row["final_destination"]),
                planned_departure_utc=when.to_pydatetime(),
                changed_departure_utc=None if pd.isna(moved) else moved.to_pydatetime(),
                delay_min=None if pd.isna(row["delay_min"]) else int(row["delay_min"]),
                is_cancelled=bool(row["is_cancelled"]),
                platform=None,  # silver has no platform column
            )
        )
    departures.sort(key=lambda d: (d.planned_departure_utc, d.event_id))
    return LiveBoard(
        generated_at=now, source="sample", replayed_from=source_day, departures=tuple(departures)
    )


def seed_board(
    store: ObjectStore,
    *,
    now: datetime,
    board_key: str = BOARD_KEY,
    source_day: date | None = None,
    silver_root: str = "",
) -> LiveBoard:
    """Build a sample board from silver and write it to ``board_key``."""
    if source_day is None:
        source_day = pick_source_day(latest_silver_day(store, silver_root), _berlin_today(now))
    rows = read_source_rows(store, source_day, silver_root)
    board = replay_board(rows, source_day=source_day, now=now)
    store.put_bytes(board_key, board_to_bytes(board), "application/gzip")
    return board


def seed_main(argv: Sequence[str] | None = None) -> int:
    """CLI for `make seed`."""
    parser = argparse.ArgumentParser(
        description="Write a sample live board: a real silver day replayed onto today."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="source day YYYY-MM-DD (default: newest silver day with today's weekday)",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    store = ObjectStore(make_s3_client(settings), settings.data_bucket)
    try:
        board = seed_board(
            store, now=datetime.now(UTC), board_key=settings.board_key, source_day=args.date
        )
    except DbDelayError as exc:
        sys.stderr.write(f"seed failed: {exc}\n")
        return 1
    stations = len({d.eva for d in board.departures})
    sys.stdout.write(
        f"sample board: {len(board.departures)} departures at {stations} stations, "
        f"replayed from {board.replayed_from}\n"
    )
    return 0
