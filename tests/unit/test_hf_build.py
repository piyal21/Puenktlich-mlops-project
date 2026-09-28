import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from dbdelay.config import Settings
from dbdelay.data.hf_backfill import bronze_prefix, build_silver_month, ingest_month
from dbdelay.data.schemas import validate_silver
from dbdelay.data.silver import quality_key, quarantine_key, silver_key
from dbdelay.data.stations import load_stations
from dbdelay.errors import DataValidationError, NotFoundError
from dbdelay.storage import ObjectStore, make_s3_client
from tests.builders import hf_frame, hf_parquet_bytes, hf_row, read_parquet_bytes

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BUCKET = "puenktlich-test"
STATIONS = load_stations(Path(__file__).resolve().parents[2] / "configs" / "stations.yaml")


@pytest.fixture
def store() -> Iterator[ObjectStore]:
    with mock_aws():
        client: S3Client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, BUCKET)


def _ingest_bytes(store: ObjectStore, month: str, payload: bytes) -> None:
    def download(path_in_repo: str, dest: Path) -> tuple[Path, str]:
        target = dest / "f.parquet"
        target.write_bytes(payload)
        return target, "rev"

    ingest_month(month, store, download=download, now=lambda: datetime(2026, 9, 27, tzinfo=UTC))


def _put_month(store: ObjectStore, month: str, *rows: dict[str, Any]) -> None:
    _ingest_bytes(store, month, hf_parquet_bytes(hf_frame(*rows)))


def _march_rows() -> list[dict[str, Any]]:
    return [
        hf_row(),  # Frankfurt 2026-03-10
        hf_row(id="2-2603100900-1", eva="08000001"),  # not our station
        hf_row(id="3-2603100900-1", train_type=None),  # quarantined
    ]


def _day(value: str) -> Any:
    return pd.Timestamp(value).date()


def test_build_writes_days_quarantine_and_report(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", *_march_rows())
    april_first = hf_row(
        id="4-2604010030-1",
        departure_planned_time=datetime(2026, 4, 1, 0, 30),
        departure_change_time=datetime(2026, 4, 1, 0, 30),
    )
    _put_month(store, "2026-04", april_first)

    report = build_silver_month(store, "2026-03", STATIONS, tmp_path)

    day10 = read_parquet_bytes(store.get_bytes(silver_key(_day("2026-03-10"))))
    day31 = read_parquet_bytes(store.get_bytes(silver_key(_day("2026-03-31"))))
    validate_silver(day10)
    validate_silver(day31)
    assert len(day10) == 1
    assert day31["ride_id"].tolist() == ["4-2604010030"]  # from the April file
    quarantined = read_parquet_bytes(store.get_bytes(quarantine_key("2026-03")))
    assert quarantined["quarantine_reason"].tolist() == ["missing_train_type"]
    stored = json.loads(store.get_bytes(quality_key("2026-03")))
    assert stored["rows_out"] == report.rows_out == 2
    assert report.edge_complete == {"prev": False, "next": True}


def test_build_is_deterministic(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", *_march_rows())
    first = build_silver_month(store, "2026-03", STATIONS, tmp_path)
    key = silver_key(_day("2026-03-10"))
    bytes_before = store.get_bytes(key)
    second = build_silver_month(store, "2026-03", STATIONS, tmp_path)
    assert second.content_hash == first.content_hash
    assert store.get_bytes(key) == bytes_before


def test_build_without_next_month_marks_edge_incomplete(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", hf_row())
    report = build_silver_month(store, "2026-03", STATIONS, tmp_path)
    assert report.edge_complete == {"prev": False, "next": False}
    assert report.rows_out == 1


def test_missing_bronze_for_the_month_raises(store: ObjectStore, tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        build_silver_month(store, "2026-03", STATIONS, tmp_path)


def test_checksum_mismatch_stops_the_month(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-03", hf_row())
    store.put_bytes(bronze_prefix("2026-03") + "data.parquet", b"corrupted")
    with pytest.raises(DataValidationError, match="checksum"):
        build_silver_month(store, "2026-03", STATIONS, tmp_path)
    assert not store.exists(silver_key(_day("2026-03-10")))


def test_bronze_missing_a_column_stops_the_month(store: ObjectStore, tmp_path: Path) -> None:
    frame = hf_frame(hf_row())
    keep = [c for c in frame.columns if c not in {"line_number", "ingested_at", "_file_month"}]
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(frame[keep], preserve_index=False), buffer)
    _ingest_bytes(store, "2026-03", buffer.getvalue())
    with pytest.raises(DataValidationError, match="line_number"):
        build_silver_month(store, "2026-03", STATIONS, tmp_path)


def test_neighbour_without_matching_rows_is_fine(store: ObjectStore, tmp_path: Path) -> None:
    _put_month(store, "2026-02", hf_row(id="7-2602100815-1", eva="08000001"))  # no station rows
    _put_month(store, "2026-03", hf_row())
    report = build_silver_month(store, "2026-03", STATIONS, tmp_path)
    assert report.edge_complete == {"prev": True, "next": False}
    assert report.rows_out == 1


def test_build_streams_bronze_instead_of_holding_it_in_memory(
    store: ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put_month(store, "2026-03", *_march_rows())
    real_get = store.get_bytes

    def get_small_only(key: str) -> bytes:
        assert not key.endswith("data.parquet"), "bronze data must be downloaded to disk"
        return real_get(key)

    monkeypatch.setattr(store, "get_bytes", get_small_only)
    report = build_silver_month(store, "2026-03", STATIONS, tmp_path)

    assert report.rows_read == 2
