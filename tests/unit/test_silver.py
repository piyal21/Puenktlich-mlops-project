import hashlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from moto import mock_aws

from dbdelay.config import Settings
from dbdelay.data.schemas import validate_silver
from dbdelay.data.silver import content_hash, make_event_id, silver_key, write_silver_month
from dbdelay.storage import ObjectStore, make_s3_client
from tests.builders import read_parquet_bytes, silver_frame

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BUCKET = "puenktlich-test"


@pytest.fixture
def store() -> Iterator[ObjectStore]:
    with mock_aws():
        client: S3Client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, BUCKET)


def test_event_id_is_sha1_of_the_frozen_format() -> None:
    planned = pd.Timestamp("2026-03-10 07:15:42", tz="UTC")
    expected = hashlib.sha1(
        b"8000105|123-2603100600|2026-03-10T07:15Z", usedforsecurity=False
    ).hexdigest()
    assert make_event_id("8000105", "123-2603100600", planned) == expected


def test_event_id_rejects_non_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="UTC"):
        make_event_id("8000105", "1-2603100600", pd.Timestamp("2026-03-10 08:15"))
    with pytest.raises(ValueError, match="UTC"):
        make_event_id(
            "8000105", "1-2603100600", pd.Timestamp("2026-03-10 08:15", tz="Europe/Berlin")
        )


def test_write_silver_month_writes_every_day_even_empty(store: ObjectStore) -> None:
    counts = write_silver_month(silver_frame(3), store, "2026-03")

    assert len(counts) == 31
    assert counts["2026-03-10"] == 3
    assert counts["2026-03-11"] == 0
    empty = read_parquet_bytes(store.get_bytes(silver_key(pd.Timestamp("2026-03-11").date())))
    assert empty.empty
    assert list(empty.columns) == list(silver_frame(1).columns)


def test_written_partition_round_trips_through_the_contract(store: ObjectStore) -> None:
    write_silver_month(silver_frame(3), store, "2026-03")

    back = read_parquet_bytes(store.get_bytes(silver_key(pd.Timestamp("2026-03-10").date())))

    validate_silver(back)
    assert content_hash(back) == content_hash(silver_frame(3))


def test_rewrite_produces_identical_bytes(store: ObjectStore) -> None:
    key = silver_key(pd.Timestamp("2026-03-10").date())
    write_silver_month(silver_frame(3), store, "2026-03")
    first = store.get_bytes(key)
    write_silver_month(silver_frame(3).iloc[::-1], store, "2026-03")  # different row order
    assert store.get_bytes(key) == first


def test_content_hash_ignores_row_order_but_not_values() -> None:
    frame = silver_frame(3)
    assert content_hash(frame) == content_hash(frame.iloc[::-1])
    changed = frame.copy()
    changed.loc[0, "train_number"] = "999"
    assert content_hash(changed) != content_hash(frame)


def test_root_prefix_is_applied() -> None:
    assert silver_key(pd.Timestamp("2026-03-01").date(), root="_t/") == (
        "_t/silver/departures/source=hf/date=2026-03-01/part-0.parquet"
    )
