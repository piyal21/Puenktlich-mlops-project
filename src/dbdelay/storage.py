"""Object storage helpers for S3 (prod) and MinIO (local) with one code path.

Setting ``STORAGE_ENDPOINT_URL`` switches boto3 to an S3-compatible endpoint.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from dbdelay.config import Settings, get_settings
from dbdelay.errors import ConfigError, ExternalServiceError, NotFoundError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

# rules.md §6.1: boto3 connect 3 s / read 10 s, max 3 attempts.
_BOTO_CONFIG = Config(
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 3, "mode": "standard"},
)
_MISSING_KEY_CODES = frozenset({"NoSuchKey", "404", "NotFound"})


def make_s3_client(settings: Settings | None = None) -> "S3Client":
    """Build an S3 client for AWS or, if an endpoint is configured, for MinIO.

    Args:
        settings: Settings to use; defaults to ``get_settings()``.

    Returns:
        A boto3 S3 client with project timeouts and retries.

    Raises:
        ConfigError: if an endpoint is set without storage credentials.
    """
    settings = settings or get_settings()
    if settings.storage_endpoint_url is None:
        # Real AWS: credentials come from the default chain (Lambda role, SSO profile).
        return boto3.client("s3", region_name=settings.aws_region, config=_BOTO_CONFIG)

    access_key = settings.storage_access_key_id
    secret_key = settings.storage_secret_access_key
    if access_key is None or secret_key is None:
        # Otherwise boto3 would fall back to ~/.aws and sign MinIO requests with real AWS keys.
        raise ConfigError(
            "STORAGE_ACCESS_KEY_ID and STORAGE_SECRET_ACCESS_KEY are required "
            "when STORAGE_ENDPOINT_URL is set"
        )
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=access_key.get_secret_value(),
        aws_secret_access_key=secret_key.get_secret_value(),
        # MinIO needs path-style addressing (no bucket subdomains on localhost).
        config=_BOTO_CONFIG.merge(Config(s3={"addressing_style": "path"})),
    )


def _is_missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code", "") in _MISSING_KEY_CODES


class ObjectStore:
    """Small wrapper around one bucket. Maps boto errors to project exceptions."""

    def __init__(self, client: "S3Client", bucket: str) -> None:
        """Bind the store to a client and bucket.

        Args:
            client: boto3 S3 client (see ``make_s3_client``).
            bucket: Bucket name.
        """
        self._client = client
        self.bucket = bucket

    def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        """Write (or overwrite) one object.

        Raises:
            ExternalServiceError: if the storage call fails.
        """
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )
        except (ClientError, BotoCoreError) as exc:
            raise ExternalServiceError(f"put s3://{self.bucket}/{key} failed") from exc

    def get_bytes(self, key: str) -> bytes:
        """Read one object fully into memory.

        Raises:
            NotFoundError: if the object does not exist.
            ExternalServiceError: for any other storage failure.
        """
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            if _is_missing(exc):
                raise NotFoundError(f"s3://{self.bucket}/{key} not found") from exc
            raise ExternalServiceError(f"get s3://{self.bucket}/{key} failed") from exc
        except BotoCoreError as exc:
            raise ExternalServiceError(f"get s3://{self.bucket}/{key} failed") from exc

    def upload_file(self, key: str, path: Path) -> None:
        """Stream a local file to one object (multipart for large files; no full read into memory).

        Raises:
            ExternalServiceError: if the upload fails.
        """
        try:
            self._client.upload_file(str(path), self.bucket, key)
        except (ClientError, BotoCoreError, S3UploadFailedError) as exc:
            raise ExternalServiceError(f"upload s3://{self.bucket}/{key} failed") from exc

    def download_file(self, key: str, path: Path) -> None:
        """Stream one object to a local file (no full read into memory).

        Raises:
            NotFoundError: if the object does not exist.
            ExternalServiceError: for any other storage failure.
        """
        try:
            self._client.download_file(self.bucket, key, str(path))
        except ClientError as exc:
            if _is_missing(exc):
                raise NotFoundError(f"s3://{self.bucket}/{key} not found") from exc
            raise ExternalServiceError(f"download s3://{self.bucket}/{key} failed") from exc
        except BotoCoreError as exc:
            raise ExternalServiceError(f"download s3://{self.bucket}/{key} failed") from exc

    def exists(self, key: str) -> bool:
        """Return whether an object exists.

        Note: HeadObject has no error body, so a missing *bucket* also reads as ``False``.

        Raises:
            ExternalServiceError: for failures other than "not found".
        """
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return False
            raise ExternalServiceError(f"head s3://{self.bucket}/{key} failed") from exc
        except BotoCoreError as exc:
            raise ExternalServiceError(f"head s3://{self.bucket}/{key} failed") from exc
        return True

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Yield all keys under a prefix, following pagination.

        Raises:
            ExternalServiceError: if listing fails.
        """
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    yield obj["Key"]
        except (ClientError, BotoCoreError) as exc:
            raise ExternalServiceError(f"list s3://{self.bucket}/{prefix} failed") from exc
