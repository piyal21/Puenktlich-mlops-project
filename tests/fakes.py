"""In-memory stand-ins for external services (unit tests only)."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from dbdelay.errors import NotFoundError


@dataclass
class FakeRun:
    experiment: str
    tags: dict[str, str]
    params: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    inputs: list[tuple[str, str, str]] = field(default_factory=list)
    status: str = "RUNNING"


@dataclass
class FakeTracker:
    """Implements `dbdelay.training.tracking.Tracker` in memory."""

    runs: dict[str, FakeRun] = field(default_factory=dict)
    files: dict[tuple[str, str], bytes] = field(default_factory=dict)
    versions: dict[str, list[str]] = field(default_factory=dict)  # model -> run id per version
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    version_tags: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    def _run(self, run_id: str) -> FakeRun:
        if run_id not in self.runs:
            raise NotFoundError(f"run {run_id} not found")
        return self.runs[run_id]

    def start_run(self, experiment: str, tags: Mapping[str, str]) -> str:
        run_id = f"run{len(self.runs) + 1}"
        self.runs[run_id] = FakeRun(experiment=experiment, tags=dict(tags))
        return run_id

    def log_params(self, run_id: str, params: Mapping[str, object]) -> None:
        self._run(run_id).params.update({k: str(v) for k, v in params.items()})

    def log_metrics(self, run_id: str, metrics: Mapping[str, float]) -> None:
        self._run(run_id).metrics.update(metrics)

    def set_tags(self, run_id: str, tags: Mapping[str, str]) -> None:
        self._run(run_id).tags.update(tags)

    def log_bytes(self, run_id: str, path: str, data: bytes) -> None:
        self._run(run_id)
        self.files[(run_id, path)] = data

    def load_bytes(self, run_id: str, path: str) -> bytes:
        try:
            return self.files[(run_id, path)]
        except KeyError:
            raise NotFoundError(f"{run_id}/{path} not found") from None

    def log_snapshot(self, run_id: str, snapshot_id: str, digest: str, source: str) -> None:
        self._run(run_id).inputs.append((snapshot_id, digest, source))

    def finish_run(self, run_id: str, *, failed: bool = False) -> None:
        self._run(run_id).status = "FAILED" if failed else "FINISHED"

    def register_version(self, model_name: str, run_id: str, artifact_path: str) -> str:
        self._run(run_id)
        self.versions.setdefault(model_name, []).append(run_id)
        return str(len(self.versions[model_name]))

    def set_alias(self, model_name: str, alias: str, version: str) -> None:
        self.aliases[(model_name, alias)] = version

    def get_alias(self, model_name: str, alias: str) -> str | None:
        return self.aliases.get((model_name, alias))

    def set_version_tags(self, model_name: str, version: str, tags: Mapping[str, str]) -> None:
        self.version_tags.setdefault((model_name, version), {}).update(tags)
