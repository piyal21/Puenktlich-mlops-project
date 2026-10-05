import gzip
import json
from datetime import UTC, timedelta
from typing import Any

import pytest

from dbdelay.errors import DataValidationError, ExternalServiceError
from dbdelay.serving.board import (
    BOARD_KEY,
    BoardSource,
    board_from_bytes,
    board_to_bytes,
    is_stale,
    select_departures,
)
from dbdelay.storage import ObjectStore
from tests.boards import FRANKFURT, MUENCHEN, NOW, departure, live_board


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _raw(board_json: dict[str, Any]) -> bytes:
    return gzip.compress(json.dumps(board_json).encode())


def test_round_trip_is_deterministic() -> None:
    board = live_board(departure(5), departure(10, delay_min=3))
    data = board_to_bytes(board)
    assert board_from_bytes(data) == board
    assert board_to_bytes(board) == data  # gzip mtime fixed


def test_times_are_normalised_to_utc() -> None:
    raw = json.loads(live_board(departure(5)).model_dump_json())
    raw["generated_at"] = "2026-10-05T15:00:00+02:00"
    raw["departures"][0]["planned_departure_utc"] = "2026-10-05T15:05:00+02:00"
    board = board_from_bytes(_raw(raw))
    assert board.generated_at == NOW
    assert board.departures[0].planned_departure_utc.tzinfo == UTC


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("generated_at",), "2026-10-05T13:00:00"),  # naive
        (("schema_version",), 2),
        (("departures", 0, "surprise"), 1),
        (("departures", 0, "eva"), "123"),
        (("departures", 0, "stop_index"), 0),
    ],
)
def test_contract_violations_are_rejected(path: tuple[Any, ...], value: object) -> None:
    raw: Any = json.loads(live_board(departure(5)).model_dump_json())
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(DataValidationError):
        board_from_bytes(_raw(raw))


def test_not_gzip_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="gzip"):
        board_from_bytes(b"{}")


def test_select_window_edges_and_order() -> None:
    board = live_board(
        departure(180),  # exactly now + 3 h -> in
        departure(181),  # out
        departure(0),  # exactly now -> in
        departure(-10, changed_departure_utc=None),  # left already (no change) -> out
        departure(
            -10, ride_id="late", changed_departure_utc=NOW + timedelta(minutes=5), delay_min=15
        ),
        departure(30, eva=MUENCHEN),  # other station
    )
    rows = select_departures(board, FRANKFURT, NOW, 3)
    minutes = [int((r.planned_departure_utc - NOW).total_seconds() // 60) for r in rows]
    assert minutes == [-10, 0, 180]
    assert rows[0].delay_min == 15


def test_cancelled_departure_stays_until_planned_time() -> None:
    board = live_board(departure(10, is_cancelled=True, changed_departure_utc=None, delay_min=None))
    assert len(select_departures(board, FRANKFURT, NOW, 1)) == 1


def test_stale_after_twenty_minutes() -> None:
    board = live_board(generated_at=NOW - timedelta(minutes=20))
    assert not is_stale(board, NOW, 1200)
    assert is_stale(board, NOW + timedelta(seconds=1), 1200)


def test_board_source_caches_for_ttl(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(BOARD_KEY, board_to_bytes(live_board(departure(5))))
    clock = FakeClock()
    source = BoardSource(s3_store, ttl_s=60, clock=clock)
    first = source.get()
    s3_store.put_bytes(BOARD_KEY, board_to_bytes(live_board(departure(5), departure(9))))
    clock.t = 59
    assert source.get() is first
    clock.t = 60
    assert len(source.get().departures) == 2


def test_missing_board_is_unavailable(s3_store: ObjectStore) -> None:
    source = BoardSource(s3_store, clock=FakeClock())
    with pytest.raises(ExternalServiceError, match="live board unavailable"):
        source.get()
    assert source.generated_at_or_none() is None


def test_board_source_serves_cached_board_when_refresh_fails(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(BOARD_KEY, board_to_bytes(live_board(departure(5))))
    clock = FakeClock()
    source = BoardSource(s3_store, clock=clock)
    good = source.get()
    s3_store.put_bytes(BOARD_KEY, b"corrupt")
    clock.t = 60
    assert source.get() is good
    assert source.generated_at_or_none() == NOW
