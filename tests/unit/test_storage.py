from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws
from pydantic import SecretStr

from dbdelay.config import Settings
from dbdelay.errors import ConfigError, ExternalServiceError, NotFoundError
from dbdelay.storage import ObjectStore, make_s3_client

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BUCKET = "puenktlich-test"


@pytest.fixture
def s3_client() -> Iterator["S3Client"]:
    with mock_aws():
        client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield client


@pytest.fixture
def store(s3_client: "S3Client") -> ObjectStore:
    return ObjectStore(s3_client, BUCKET)


def test_put_then_get_roundtrip(store: ObjectStore) -> None:
    store.put_bytes("live/boards/latest.json.gz", b"payload", content_type="application/gzip")
    assert store.get_bytes("live/boards/latest.json.gz") == b"payload"


def test_put_overwrites_existing_object(store: ObjectStore) -> None:
    store.put_bytes("a.txt", b"first")
    store.put_bytes("a.txt", b"second")
    assert store.get_bytes("a.txt") == b"second"


def test_get_missing_key_raises_not_found(store: ObjectStore) -> None:
    with pytest.raises(NotFoundError):
        store.get_bytes("does/not/exist")


def test_exists(store: ObjectStore) -> None:
    store.put_bytes("control/retrain_requested.json", b"{}")
    assert store.exists("control/retrain_requested.json")
    assert not store.exists("control/other.json")


def test_iter_keys_filters_by_prefix_and_paginates(store: ObjectStore) -> None:
    # > 1000 keys forces a second list_objects_v2 page.
    for i in range(1005):
        store.put_bytes(f"silver/date=2026-01-01/part-{i:04d}.parquet", b"x")
    store.put_bytes("bronze/other.jsonl.gz", b"x")
    keys = list(store.iter_keys("silver/"))
    assert len(keys) == 1005
    assert all(k.startswith("silver/") for k in keys)


def test_missing_bucket_raises_external_service_error(s3_client: "S3Client") -> None:
    store = ObjectStore(s3_client, "no-such-bucket")
    with pytest.raises(ExternalServiceError):
        store.get_bytes("x")
    with pytest.raises(ExternalServiceError):
        store.put_bytes("x", b"x")
    with pytest.raises(ExternalServiceError):
        list(store.iter_keys())


def test_exists_reraises_non_missing_errors(
    store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(**_: object) -> None:
        raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    monkeypatch.setattr(store._client, "head_object", forbidden)
    with pytest.raises(ExternalServiceError):
        store.exists("x")


def test_connection_errors_are_wrapped(store: ObjectStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(**_: object) -> None:
        raise EndpointConnectionError(endpoint_url="http://localhost:9000")

    monkeypatch.setattr(store._client, "get_object", unreachable)
    monkeypatch.setattr(store._client, "head_object", unreachable)
    with pytest.raises(ExternalServiceError):
        store.get_bytes("x")
    with pytest.raises(ExternalServiceError):
        store.exists("x")


def test_endpoint_override_uses_path_style_and_given_credentials() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        storage_endpoint_url="http://localhost:9000",
        storage_access_key_id=SecretStr("minio-user"),
        storage_secret_access_key=SecretStr("minio-pass"),
    )
    client = make_s3_client(settings)
    assert client.meta.endpoint_url == "http://localhost:9000"
    config: Any = client.meta.config  # botocore stubs omit Config attributes
    assert config.s3 == {"addressing_style": "path"}
    credentials = client._request_signer._credentials  # type: ignore[attr-defined]
    assert credentials.access_key == "minio-user"


def test_client_has_project_timeouts() -> None:
    client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
    config: Any = client.meta.config  # botocore stubs omit Config attributes
    assert config.connect_timeout == 3
    assert config.read_timeout == 10
    assert client.meta.endpoint_url.startswith("https://s3.")


def test_endpoint_without_credentials_raises_config_error() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, storage_endpoint_url="http://localhost:9000"
    )
    with pytest.raises(ConfigError, match="STORAGE_ACCESS_KEY_ID"):
        make_s3_client(settings)
