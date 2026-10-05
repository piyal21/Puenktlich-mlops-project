from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from dbdelay.config import get_settings
from dbdelay.data.silver import SILVER_ARROW_SCHEMA, make_event_id, silver_key, to_parquet_bytes
from dbdelay.serving.board import BOARD_KEY, board_from_bytes
from dbdelay.serving.seed import (
    pick_source_day,
    read_source_rows,
    replay_board,
    seed_board,
    seed_main,
)
from dbdelay.storage import ObjectStore
from tests.builders import silver_frame
from tests.unit.conftest import TEST_BUCKET


def _silver(planned_utc: list[str], *, cancelled: int | None = None) -> pd.DataFrame:
    """Silver rows at the given UTC times, each 4 min late (row ``cancelled`` cancelled)."""
    n = len(planned_utc)
    df = silver_frame(n)
    planned = pd.Series(pd.to_datetime(planned_utc, utc=True), dtype="datetime64[us, UTC]")
    df["ride_id"] = [f"{500 + i}-2603280600" for i in range(n)]
    df["planned_departure_utc"] = planned
    df["changed_departure_utc"] = planned + np.timedelta64(4, "m")
    df["delay_min"] = pd.Series([4] * n, dtype="Int16")
    df["is_late"] = pd.Series([False] * n, dtype="boolean")
    if cancelled is not None:
        df.loc[cancelled, "is_cancelled"] = True
        df.loc[cancelled, "changed_departure_utc"] = pd.NaT
        df.loc[cancelled, "delay_min"] = pd.NA
        df.loc[cancelled, "is_late"] = pd.NA
    return df


def _put(store: ObjectStore, df: pd.DataFrame) -> None:
    for day, part in df.groupby(df["planned_departure_utc"].dt.date):
        store.put_bytes(silver_key(day), to_parquet_bytes(part, SILVER_ARROW_SCHEMA))


def test_pick_source_day_matches_weekday_and_leaves_a_next_day() -> None:
    latest = date(2026, 8, 31)  # Monday
    assert pick_source_day(latest, date(2026, 10, 5)) == date(2026, 8, 24)  # Monday
    assert pick_source_day(latest, date(2026, 10, 6)) == date(2026, 8, 25)  # Tuesday
    assert pick_source_day(latest, date(2026, 10, 4)) == date(2026, 8, 30)  # Sunday


def test_replay_keeps_wall_clock_across_dst() -> None:
    rows = _silver(["2026-03-28T09:00:00Z"])  # Sat 10:00 CET
    now = datetime(2026, 4, 4, 8, 0, tzinfo=UTC)  # Sat 10:00 CEST
    board = replay_board(rows, source_day=date(2026, 3, 28), now=now)
    [row] = board.departures
    assert row.planned_departure_utc == datetime(2026, 4, 4, 8, 0, tzinfo=UTC)
    assert row.changed_departure_utc == row.planned_departure_utc + timedelta(minutes=4)
    assert row.event_id == make_event_id(
        row.eva, row.ride_id, pd.Timestamp(row.planned_departure_utc)
    )
    assert (board.source, board.replayed_from, board.generated_at) == (
        "sample",
        date(2026, 3, 28),
        now,
    )
    assert row.platform is None


def test_replay_window_is_minus_30_min_to_plus_6_h() -> None:
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    week_ago = now - timedelta(days=7)
    offsets = [-31, -30, 360, 361]
    rows = _silver([(week_ago + timedelta(minutes=m)).isoformat() for m in offsets])
    board = replay_board(rows, source_day=date(2026, 9, 28), now=now)
    kept = [int((d.planned_departure_utc - now).total_seconds() // 60) for d in board.departures]
    assert kept == [-30, 360]


def test_next_source_day_lands_tomorrow() -> None:
    rows = _silver(["2026-08-24T23:00:00Z"])  # Tue 2026-08-25 01:00 CEST
    now = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)  # Mon 23:00 CEST
    board = replay_board(rows, source_day=date(2026, 8, 24), now=now)
    [row] = board.departures
    assert row.planned_departure_utc == datetime(2026, 10, 5, 23, 0, tzinfo=UTC)


def test_cancelled_rows_stay_cancelled() -> None:
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    rows = _silver([(now - timedelta(days=7, minutes=-10)).isoformat()], cancelled=0)
    [row] = replay_board(rows, source_day=date(2026, 9, 28), now=now).departures
    assert row.is_cancelled
    assert row.changed_departure_utc is None
    assert row.delay_min is None


def test_read_source_rows_uses_berlin_dates(s3_store: ObjectStore) -> None:
    _put(
        s3_store,
        _silver(
            [
                "2026-08-23T21:59:00Z",  # 23:59 on the 23rd local -> out
                "2026-08-23T22:00:00Z",  # 00:00 on the 24th local -> in
                "2026-08-25T21:59:00Z",  # 23:59 on the 25th local -> in
                "2026-08-25T22:00:00Z",  # 00:00 on the 26th local -> out
            ]
        ),
    )
    rows = read_source_rows(s3_store, date(2026, 8, 24))
    assert len(rows) == 2


def test_seed_board_writes_the_board(s3_store: ObjectStore) -> None:
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    _put(s3_store, _silver(["2026-09-28T13:30:00Z", "2026-09-29T08:00:00Z"]))
    board = seed_board(s3_store, now=now, source_day=date(2026, 9, 28))
    assert board_from_bytes(s3_store.get_bytes(BOARD_KEY)) == board
    assert len(board.departures) == 1


def test_seed_main_writes_board_and_reports(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)
    get_settings.cache_clear()
    start = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    _put(s3_store, _silver([(start + timedelta(days=d)).isoformat() for d in range(15)]))
    assert seed_main([]) == 0
    board = board_from_bytes(s3_store.get_bytes(BOARD_KEY))
    assert board.replayed_from is not None
    today = datetime.now(UTC).astimezone(ZoneInfo("Europe/Berlin")).date()
    assert board.replayed_from.weekday() == today.weekday()
    assert "sample board:" in capsys.readouterr().out


def test_seed_main_without_silver_fails_cleanly(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)
    get_settings.cache_clear()
    assert seed_main([]) == 1
    assert "seed failed" in capsys.readouterr().err
