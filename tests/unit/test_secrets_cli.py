from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from dbdelay.live.secrets_cli import put_main

ARGS = ["--client-id-param", "/p/client_id", "--key-param", "/p/api_key", "--region", "eu-north-1"]


@pytest.fixture
def ssm() -> Iterator[Any]:
    with mock_aws():
        yield boto3.client("ssm", region_name="eu-north-1")


def test_put_secrets_writes_secure_strings_without_printing(
    ssm: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DB_API_CLIENT_ID", "cid-secret-value")
    monkeypatch.setenv("DB_API_KEY", "key-secret-value")
    assert put_main(ARGS) == 0
    params = ssm.get_parameters(Names=["/p/client_id", "/p/api_key"], WithDecryption=True)
    assert {p["Name"]: (p["Type"], p["Value"]) for p in params["Parameters"]} == {
        "/p/client_id": ("SecureString", "cid-secret-value"),
        "/p/api_key": ("SecureString", "key-secret-value"),
    }
    out = capsys.readouterr()
    assert "secret-value" not in out.out + out.err
    assert "/p/client_id" in out.out


def test_put_secrets_overwrites_the_placeholder(ssm: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    ssm.put_parameter(Name="/p/api_key", Value="set-by-make", Type="SecureString")
    monkeypatch.setenv("DB_API_CLIENT_ID", "cid")
    monkeypatch.setenv("DB_API_KEY", "key")
    assert put_main(ARGS) == 0
    value = ssm.get_parameter(Name="/p/api_key", WithDecryption=True)["Parameter"]["Value"]
    assert value == "key"


def test_put_secrets_without_env_values_refuses(
    ssm: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert put_main(ARGS) == 1
    assert "DB_API_CLIENT_ID" in capsys.readouterr().err
    assert ssm.describe_parameters()["Parameters"] == []
