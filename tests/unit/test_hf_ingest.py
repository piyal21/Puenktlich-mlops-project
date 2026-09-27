import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from moto import mock_aws

from dbdelay.config import Settings
from dbdelay.data import hf_backfill
from dbdelay.data.hf_backfill import BronzeManifest, bronze_prefix, ingest_month, load_manifest
from dbdelay.errors import ConfigError, ExternalServiceError
from dbdelay.storage import ObjectStore, make_s3_client

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

BUCKET = "puenktlich-test"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)


@pytest.fixture
def store() -> Iterator[ObjectStore]:
    with mock_aws():
        client: S3Client = make_s3_client(Settings(_env_file=None))  # type: ignore[call-arg]
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        yield ObjectStore(client, BUCKET)


class FakeDownloader:
    def __init__(self, payload: bytes = b"PAR1-fake") -> None:
        self.payload = payload
        self.calls: list[str] = []

    def __call__(self, path_in_repo: str, dest: Path) -> tuple[Path, str]:
        self.calls.append(path_in_repo)
        target = dest / "file.parquet"
        target.write_bytes(self.payload)
        return target, "rev123"


def test_ingest_stores_file_and_manifest(store: ObjectStore) -> None:
    fake = FakeDownloader()
    manifest = ingest_month("2026-03", store, download=fake, now=lambda: NOW)

    prefix = bronze_prefix("2026-03")
    assert store.get_bytes(prefix + "data.parquet") == b"PAR1-fake"
    assert fake.calls == ["monthly_processed_data/data-2026-03.parquet"]
    assert manifest == BronzeManifest(
        month="2026-03",
        hf_repo="piebro/deutsche-bahn-data",
        hf_path="monthly_processed_data/data-2026-03.parquet",
        hf_revision="rev123",
        sha256=hashlib.sha256(b"PAR1-fake").hexdigest(),
        size_bytes=9,
        downloaded_at=NOW,
    )
    assert load_manifest(store, "2026-03") == manifest


def test_existing_bronze_is_not_downloaded_again(store: ObjectStore) -> None:
    first = ingest_month("2026-03", store, download=FakeDownloader(), now=lambda: NOW)
    fake = FakeDownloader(b"other")
    again = ingest_month("2026-03", store, download=fake, now=lambda: datetime.now(UTC))
    assert fake.calls == []
    assert again == first


def test_force_downloads_again(store: ObjectStore) -> None:
    ingest_month("2026-03", store, download=FakeDownloader(), now=lambda: NOW)
    fake = FakeDownloader(b"newer")
    manifest = ingest_month("2026-03", store, force=True, download=fake, now=lambda: NOW)
    assert fake.calls
    assert manifest.size_bytes == 5


def test_invalid_month_is_rejected_before_downloading(store: ObjectStore) -> None:
    fake = FakeDownloader()
    with pytest.raises(ConfigError):
        ingest_month("2026-3", store, download=fake)
    assert fake.calls == []


def test_hf_download_pins_the_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeApi:
        def dataset_info(self, repo_id: str) -> object:
            assert repo_id == "piebro/deutsche-bahn-data"
            return type("Info", (), {"sha": "abc123"})()

    seen: dict[str, object] = {}

    def fake_download(repo_id: str, filename: str, **kwargs: object) -> str:
        seen.update(kwargs, filename=filename)
        target = tmp_path / "x.parquet"
        target.write_bytes(b"x")
        return str(target)

    monkeypatch.setattr(hf_backfill, "HfApi", FakeApi)
    monkeypatch.setattr(hf_backfill, "hf_hub_download", fake_download)

    path, revision = hf_backfill.hf_download(
        "monthly_processed_data/data-2026-03.parquet", tmp_path
    )

    assert revision == "abc123"
    assert seen["revision"] == "abc123"
    assert seen["repo_type"] == "dataset"
    assert path.read_bytes() == b"x"


def test_hf_download_errors_become_external_service_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenApi:
        def dataset_info(self, repo_id: str) -> object:
            raise OSError("network down")

    monkeypatch.setattr(hf_backfill, "HfApi", BrokenApi)
    with pytest.raises(ExternalServiceError):
        hf_backfill.hf_download("monthly_processed_data/data-2026-03.parquet", tmp_path)
