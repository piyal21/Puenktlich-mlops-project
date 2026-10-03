"""Experiment tracking and model registry behind a small protocol.

`MlflowTracker` talks to the MLflow server (artifacts go through its proxy, so clients need no
storage credentials); unit tests use an in-memory fake with the same methods.
"""

import json
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, TypeVar

import mlflow.artifacts
from mlflow import MlflowClient
from mlflow.entities import Dataset, DatasetInput, InputTag, Metric, Param, RunTag
from mlflow.exceptions import MlflowException

from dbdelay.errors import ExternalServiceError

T = TypeVar("T")
MAX_DIGEST = 36  # MLflow's limit for dataset digests
_MISSING_CODES = frozenset({"RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"})


class Tracker(Protocol):
    def start_run(self, experiment: str, tags: Mapping[str, str]) -> str: ...
    def log_params(self, run_id: str, params: Mapping[str, object]) -> None: ...
    def log_metrics(self, run_id: str, metrics: Mapping[str, float]) -> None: ...
    def set_tags(self, run_id: str, tags: Mapping[str, str]) -> None: ...
    def log_bytes(self, run_id: str, path: str, data: bytes) -> None: ...
    def load_bytes(self, run_id: str, path: str) -> bytes: ...
    def log_snapshot(self, run_id: str, snapshot_id: str, digest: str, source: str) -> None: ...
    def finish_run(self, run_id: str, *, failed: bool = False) -> None: ...
    def register_version(self, model_name: str, run_id: str, artifact_path: str) -> str: ...
    def set_alias(self, model_name: str, alias: str, version: str) -> None: ...
    def get_alias(self, model_name: str, alias: str) -> str | None: ...
    def set_version_tags(self, model_name: str, version: str, tags: Mapping[str, str]) -> None: ...


def dataset_digest(content_hash: str) -> str:
    return content_hash[:MAX_DIGEST]


class MlflowTracker:
    """`Tracker` backed by an MLflow tracking + registry server.

    Raises ``ExternalServiceError`` (from every method) when MLflow calls fail.
    """

    def __init__(self, tracking_uri: str) -> None:
        self._uri = tracking_uri
        self._client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)

    def _call(self, what: str, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except MlflowException as exc:
            raise ExternalServiceError(f"mlflow {what} failed") from exc

    def start_run(self, experiment: str, tags: Mapping[str, str]) -> str:
        def run() -> str:
            found = self._client.get_experiment_by_name(experiment)
            experiment_id = (
                found.experiment_id if found else self._client.create_experiment(experiment)
            )
            return str(self._client.create_run(experiment_id, tags=dict(tags)).info.run_id)

        return self._call("start_run", run)

    def log_params(self, run_id: str, params: Mapping[str, object]) -> None:
        batch = [Param(key, str(value)) for key, value in sorted(params.items())]
        self._call("log_params", lambda: self._client.log_batch(run_id, params=batch))

    def log_metrics(self, run_id: str, metrics: Mapping[str, float]) -> None:
        now = int(time.time() * 1000)
        batch = [Metric(key, float(value), now, 0) for key, value in sorted(metrics.items())]
        self._call("log_metrics", lambda: self._client.log_batch(run_id, metrics=batch))

    def set_tags(self, run_id: str, tags: Mapping[str, str]) -> None:
        batch = [RunTag(key, value) for key, value in sorted(tags.items())]
        self._call("set_tags", lambda: self._client.log_batch(run_id, tags=batch))

    def log_bytes(self, run_id: str, path: str, data: bytes) -> None:
        directory, _, name = path.rpartition("/")
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / name
            local.write_bytes(data)
            self._call(
                "log_artifact",
                lambda: self._client.log_artifact(run_id, str(local), directory or None),
            )

    def load_bytes(self, run_id: str, path: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            local = self._call(
                "download_artifacts",
                lambda: mlflow.artifacts.download_artifacts(
                    run_id=run_id, artifact_path=path, dst_path=tmp, tracking_uri=self._uri
                ),
            )
            return Path(local).read_bytes()

    def log_snapshot(self, run_id: str, snapshot_id: str, digest: str, source: str) -> None:
        dataset = Dataset(
            name=snapshot_id,
            digest=dataset_digest(digest),
            source_type="s3",
            source=json.dumps({"uri": source}),
        )
        tag = InputTag("mlflow.data.context", "training")
        self._call(
            "log_inputs",
            lambda: self._client.log_inputs(run_id, [DatasetInput(dataset, [tag])]),
        )

    def finish_run(self, run_id: str, *, failed: bool = False) -> None:
        status = "FAILED" if failed else "FINISHED"
        self._call("set_terminated", lambda: self._client.set_terminated(run_id, status=status))

    def register_version(self, model_name: str, run_id: str, artifact_path: str) -> str:
        def register() -> str:
            try:
                self._client.create_registered_model(model_name)
            except MlflowException as exc:
                if exc.error_code != "RESOURCE_ALREADY_EXISTS":
                    raise
            source = f"{self._client.get_run(run_id).info.artifact_uri}/{artifact_path}"
            return str(self._client.create_model_version(model_name, source, run_id=run_id).version)

        return self._call("register_version", register)

    def set_alias(self, model_name: str, alias: str, version: str) -> None:
        self._call(
            "set_alias",
            lambda: self._client.set_registered_model_alias(model_name, alias, version),
        )

    def get_alias(self, model_name: str, alias: str) -> str | None:
        """Version behind ``alias``, or ``None`` if the model or alias does not exist."""
        try:
            return str(self._client.get_model_version_by_alias(model_name, alias).version)
        except MlflowException as exc:
            if exc.error_code in _MISSING_CODES:
                return None
            raise ExternalServiceError("mlflow get_alias failed") from exc

    def set_version_tags(self, model_name: str, version: str, tags: Mapping[str, str]) -> None:
        def tag() -> None:
            for key, value in sorted(tags.items()):
                self._client.set_model_version_tag(model_name, version, key, value)

        self._call("set_version_tags", tag)
