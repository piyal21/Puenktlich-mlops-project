from types import SimpleNamespace

import pandas as pd
import pytest

import seed_handler
from dbdelay.data.silver import SILVER_ARROW_SCHEMA, silver_key, to_parquet_bytes
from dbdelay.errors import DataValidationError
from dbdelay.serving.board import BOARD_KEY, board_from_bytes
from dbdelay.serving.samples import SAMPLE_DAYS
from dbdelay.storage import ObjectStore
from tests.builders import silver_frame
from tests.unit.conftest import TEST_BUCKET

CTX = SimpleNamespace(aws_request_id="ctx")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_BUCKET", TEST_BUCKET)


def _put_samples(store: ObjectStore) -> None:
    for day in SAMPLE_DAYS:
        df = silver_frame(2)
        planned = pd.Timestamp(f"{day} 12:00", tz="UTC")
        df["planned_departure_utc"] = pd.Series([planned] * 2, dtype="datetime64[us, UTC]")
        df["changed_departure_utc"] = df["planned_departure_utc"]
        store.put_bytes(silver_key(day, "samples/"), to_parquet_bytes(df, SILVER_ARROW_SCHEMA))


def test_seed_handler_writes_sample_board(s3_store: ObjectStore) -> None:
    _put_samples(s3_store)
    result = seed_handler.handler({}, CTX)
    board = board_from_bytes(s3_store.get_bytes(BOARD_KEY))
    assert board.source == "sample"
    assert board.replayed_from is not None
    assert result == {
        "departures": len(board.departures),
        "replayed_from": board.replayed_from.isoformat(),
    }


def test_seed_handler_without_samples_raises_and_keeps_board(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(BOARD_KEY, b"last good board")
    with pytest.raises(DataValidationError):
        seed_handler.handler({}, CTX)
    assert s3_store.get_bytes(BOARD_KEY) == b"last good board"
