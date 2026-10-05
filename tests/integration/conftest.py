"""Shared integration fixtures (local compose stack)."""

import uuid
from collections.abc import Iterator

import pytest

from dbdelay.config import get_settings
from dbdelay.storage import ObjectStore, make_s3_client


@pytest.fixture
def scratch() -> Iterator[tuple[ObjectStore, str]]:
    """The data bucket plus a unique prefix that is deleted afterwards."""
    settings = get_settings()
    client = make_s3_client(settings)
    root = f"_integration/{uuid.uuid4()}/"
    yield ObjectStore(client, settings.data_bucket), root
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.data_bucket, Prefix=root):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=settings.data_bucket, Key=obj["Key"])
