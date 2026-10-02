import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.run_baseline import run_baseline
from dbdelay.training.split import build_snapshot, snapshot_prefix
from tests.builders import MARCH_CONFIG, put_march_silver

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def scratch() -> Iterator[tuple[ObjectStore, str]]:
    settings = get_settings()
    client = make_s3_client(settings)
    root = f"_integration/{uuid.uuid4()}/"
    yield ObjectStore(client, settings.data_bucket), root
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.data_bucket, Prefix=root):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=settings.data_bucket, Key=obj["Key"])


def test_snapshot_twice_gives_same_id_and_bytes(
    scratch: tuple[ObjectStore, str], tmp_path: Path
) -> None:
    store, root = scratch
    put_march_silver(store, root)
    first = build_snapshot(store, MARCH_CONFIG, tmp_path / "a", root=root)
    key = snapshot_prefix(first.snapshot_id, root) + "train.parquet"
    before = store.get_bytes(key)
    second = build_snapshot(store, MARCH_CONFIG, tmp_path / "b", root=root)
    assert second == first
    assert store.get_bytes(key) == before


def test_run_baseline_end_to_end(scratch: tuple[ObjectStore, str], tmp_path: Path) -> None:
    store, root = scratch
    put_march_silver(store, root)
    stations = load_stations(REPO / "configs" / "stations.yaml")
    report = run_baseline(store, MARCH_CONFIG, stations, tmp_path, root=root)
    prefix = snapshot_prefix(report.snapshot_id, root) + "baseline/"
    for name in ("feature_spec.json", "baseline.json", "metrics.json"):
        assert store.exists(prefix + name)
