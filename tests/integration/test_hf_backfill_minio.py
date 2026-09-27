"""End-to-end month build against the local MinIO (`make up`)."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from dbdelay.config import Settings
from dbdelay.data.hf_backfill import build_silver_month, ingest_month
from dbdelay.data.schemas import validate_silver
from dbdelay.data.silver import silver_key
from dbdelay.data.stations import load_stations
from dbdelay.storage import ObjectStore, make_s3_client
from tests.builders import hf_frame, hf_parquet_bytes, hf_row, read_parquet_bytes

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIONS = load_stations(REPO_ROOT / "configs" / "stations.yaml")


@pytest.fixture
def scratch() -> Iterator[tuple[ObjectStore, str]]:
    settings = Settings(_env_file=REPO_ROOT / ".env")  # type: ignore[call-arg]
    if settings.storage_endpoint_url is None:
        pytest.fail("STORAGE_ENDPOINT_URL must point to local MinIO (see .env.example)")
    client = make_s3_client(settings)
    root = f"_integration/{uuid.uuid4()}/"
    yield ObjectStore(client, settings.data_bucket), root
    # Test-only cleanup of our own scratch prefix (the shared bucket holds real data).
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.data_bucket, Prefix=root):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=settings.data_bucket, Key=obj["Key"])


def test_month_builds_twice_identically(scratch: tuple[ObjectStore, str], tmp_path: Path) -> None:
    store, root = scratch
    late = hf_row(
        id="5-2603101000-3",
        delay_in_min=9,
        departure_change_time=datetime(2026, 3, 10, 8, 24),
    )
    payload = hf_parquet_bytes(hf_frame(hf_row(), late))

    def download(path_in_repo: str, dest: Path) -> tuple[Path, str]:
        target = dest / "f.parquet"
        target.write_bytes(payload)
        return target, "rev"

    ingest_month(
        "2026-03",
        store,
        download=download,
        now=lambda: datetime(2026, 9, 27, tzinfo=UTC),
        root=root,
    )

    first = build_silver_month(store, "2026-03", STATIONS, tmp_path, root=root)
    key = silver_key(pd.Timestamp("2026-03-10").date(), root=root)
    first_bytes = store.get_bytes(key)
    second = build_silver_month(store, "2026-03", STATIONS, tmp_path, root=root)

    assert first.rows_out == second.rows_out == 2
    assert first.content_hash == second.content_hash
    assert store.get_bytes(key) == first_bytes
    day = read_parquet_bytes(first_bytes)
    validate_silver(day)
    assert sorted(day["is_late"].tolist()) == [False, True]
