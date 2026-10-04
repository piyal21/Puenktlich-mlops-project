import os
from typing import Any

import pytest
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import RESOURCE_DOES_NOT_EXIST

from dbdelay.errors import ExternalServiceError
from dbdelay.training.tracking import (
    CLIENT_ENV_DEFAULTS,
    MAX_DIGEST,
    MlflowTracker,
    Tracker,
    dataset_digest,
)
from tests.fakes import FakeTracker


def test_fake_tracker_satisfies_protocol() -> None:
    tracker: Tracker = FakeTracker()
    run_id = tracker.start_run("exp", {"a": "b"})
    tracker.log_bytes(run_id, "bundle/model.txt", b"x")
    assert tracker.load_bytes(run_id, "bundle/model.txt") == b"x"
    assert tracker.register_version("m", run_id, "bundle") == "1"


class _BrokenClient:
    def __getattr__(self, name: str) -> Any:
        def fail(*args: object, **kwargs: object) -> None:
            raise MlflowException("server down")

        return fail


def test_mlflow_errors_become_external_service_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = MlflowTracker("http://127.0.0.1:9")
    monkeypatch.setattr(tracker, "_client", _BrokenClient())
    with pytest.raises(ExternalServiceError, match="set_tags"):
        tracker.set_tags("run", {"a": "b"})
    with pytest.raises(ExternalServiceError, match="register_version"):
        tracker.register_version("m", "run", "bundle")


class _NoAlias:
    def get_model_version_by_alias(self, name: str, alias: str) -> None:
        raise MlflowException("nope", error_code=RESOURCE_DOES_NOT_EXIST)


def test_missing_alias_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = MlflowTracker("http://127.0.0.1:9")
    monkeypatch.setattr(tracker, "_client", _NoAlias())
    assert tracker.get_alias("m", "champion") is None


def test_digest_is_cut_to_mlflow_limit() -> None:
    assert len(dataset_digest("a" * 64)) == MAX_DIGEST


def test_tracker_sets_client_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Presigned URLs from the server point at the in-cluster MinIO host (minio:9000), which is
    # not reachable from the developer machine -> transfers go through the proxy. MLflow's
    # emoji "View run" print crashes cp1252 consoles on Windows -> suppressed.
    for name in CLIENT_ENV_DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    MlflowTracker("http://127.0.0.1:9")
    assert {name: os.environ[name] for name in CLIENT_ENV_DEFAULTS} == {
        "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "false",
        "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD": "false",
        "MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT": "true",
    }


def test_tracker_keeps_explicit_client_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD", "true")
    MlflowTracker("http://127.0.0.1:9")
    assert os.environ["MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD"] == "true"
