"""Unit-test isolation: no real `.env`, no real AWS, fresh settings per test."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from moto import mock_aws

from dbdelay.config import Settings, get_settings
from dbdelay.storage import ObjectStore, make_s3_client

TEST_BUCKET = "puenktlich-test"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    # Run from an empty dir so the developer's `.env` is never read.
    monkeypatch.chdir(tmp_path)
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    # Fake credentials so boto3 can never reach a real AWS account.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def s3_store() -> Iterator[ObjectStore]:
    """An empty moto-backed bucket."""
    with mock_aws():
        client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=TEST_BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, TEST_BUCKET)
