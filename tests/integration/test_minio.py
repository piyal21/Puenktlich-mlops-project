"""Round-trip against the local MinIO from `make up`. Run with `make test-integration`."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbdelay.config import Settings
from dbdelay.errors import NotFoundError
from dbdelay.storage import ObjectStore, make_s3_client

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


PREFIX = "_integration/"


@pytest.fixture
def store() -> Iterator[ObjectStore]:
    settings = Settings(_env_file=REPO_ROOT / ".env")  # type: ignore[call-arg]
    if settings.storage_endpoint_url is None:
        pytest.fail("STORAGE_ENDPOINT_URL must point to local MinIO (see .env.example)")
    client = make_s3_client(settings)
    yield ObjectStore(client, settings.data_bucket)
    # Test-only cleanup of our own scratch prefix (the shared bucket also holds real data).
    listing = client.list_objects_v2(Bucket=settings.data_bucket, Prefix=PREFIX)
    for obj in listing.get("Contents", []):
        client.delete_object(Bucket=settings.data_bucket, Key=obj["Key"])


def test_minio_roundtrip(store: ObjectStore) -> None:
    key = f"{PREFIX}{uuid.uuid4()}.txt"
    store.put_bytes(key, b"hello minio", content_type="text/plain")
    assert store.exists(key)
    assert store.get_bytes(key) == b"hello minio"
    assert key in list(store.iter_keys(PREFIX))


def test_minio_missing_key(store: ObjectStore) -> None:
    with pytest.raises(NotFoundError):
        store.get_bytes(f"{PREFIX}{uuid.uuid4()}-missing")
