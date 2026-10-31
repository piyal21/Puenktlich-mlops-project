from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws

from dbdelay.config import Settings
from dbdelay.errors import ExternalServiceError
from dbdelay.registry.pointer import (
    NONE_VALUE,
    ObjectStorePointer,
    SsmPointer,
    make_pointer,
)
from dbdelay.storage import ObjectStore

CHAMP = "/puenktlich/prod/model/champion_version"
PREV = "/puenktlich/prod/model/previous_version"


@pytest.fixture
def ssm() -> Iterator[Any]:
    """Moto SSM with both pointer parameters created the way Terraform creates them."""
    with mock_aws():
        client = boto3.client("ssm", region_name="eu-north-1")
        for name in (CHAMP, PREV):
            client.put_parameter(Name=name, Value=NONE_VALUE, Type="String")
        yield client


def _value(client: Any, name: str) -> str:
    return str(client.get_parameter(Name=name)["Parameter"]["Value"])


def test_ssm_pointer_champion_none_is_none(ssm: Any) -> None:
    assert SsmPointer(ssm, CHAMP).get() is None


def test_ssm_pointer_missing_parameter_is_none() -> None:
    with mock_aws():
        client = boto3.client("ssm", region_name="eu-north-1")
        assert SsmPointer(client, CHAMP).get() is None


def test_ssm_pointer_set_then_get(ssm: Any) -> None:
    pointer = SsmPointer(ssm, CHAMP)
    state = pointer.set("2", "1")
    assert (state.champion_version, state.previous_version) == ("2", "1")
    read = pointer.get()
    assert read is not None
    assert (read.champion_version, read.previous_version) == ("2", "1")
    assert read.updated_at.tzinfo is not None


def test_ssm_pointer_set_without_previous_writes_none(ssm: Any) -> None:
    pointer = SsmPointer(ssm, CHAMP)
    pointer.set("1", None)
    assert _value(ssm, PREV) == NONE_VALUE
    read = pointer.get()
    assert read is not None
    assert read.previous_version is None


def test_ssm_pointer_derives_previous_param_name(ssm: Any) -> None:
    SsmPointer(ssm, CHAMP).set("3", "2")
    assert _value(ssm, CHAMP) == "3"
    assert _value(ssm, PREV) == "2"


def test_ssm_pointer_aws_error_is_external_service_error() -> None:
    client = boto3.client("ssm", region_name="eu-north-1")
    with Stubber(client) as stub:
        stub.add_client_error("get_parameters", service_error_code="AccessDeniedException")
        with pytest.raises(ExternalServiceError):
            SsmPointer(client, CHAMP).get()


def test_make_pointer_picks_ssm_for_slash_param(s3_store: ObjectStore) -> None:
    settings = Settings(_env_file=None, model_pointer_param=CHAMP)  # type: ignore[call-arg]
    assert isinstance(make_pointer(settings, s3_store), SsmPointer)


def test_make_pointer_picks_object_store_otherwise(s3_store: ObjectStore) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert isinstance(make_pointer(settings, s3_store), ObjectStorePointer)
