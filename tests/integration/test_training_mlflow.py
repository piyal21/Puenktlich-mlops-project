import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.registry.release import CHAMPION, rollback
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.config import RegistryConfig, TrainingConfig
from dbdelay.training.pipeline import PipelineContext, run_training_pipeline
from dbdelay.training.tracking import MlflowTracker
from tests.builders import SIGNAL_CONFIG, WEAK_LIGHTGBM, put_signal_silver

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
STATIONS = tuple(load_stations(REPO / "configs" / "stations.yaml"))


@pytest.fixture
def scratch() -> Iterator[tuple[ObjectStore, str]]:
    settings = get_settings()
    client = make_s3_client(settings)
    root = f"_integration/{uuid.uuid4()}/"
    yield ObjectStore(client, settings.data_bucket), root
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.data_bucket, Prefix=root):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=settings.data_bucket, Key=obj["Key"])


def test_tracker_round_trip() -> None:
    tracker = MlflowTracker(get_settings().mlflow_tracking_uri)
    name = f"it-{uuid.uuid4().hex[:8]}"
    run_id = tracker.start_run(name, {"k": "v"})
    tracker.log_bytes(run_id, "bundle/a.txt", b"hello")
    assert tracker.load_bytes(run_id, "bundle/a.txt") == b"hello"
    tracker.log_snapshot(run_id, "2026-03-31_abcdef12", "f" * 64, "s3://bucket/x/")
    tracker.log_params(run_id, {"p": 1})
    tracker.log_metrics(run_id, {"m": 0.5})
    assert tracker.get_alias(name, CHAMPION) is None
    version = tracker.register_version(name, run_id, "bundle")
    assert tracker.get_alias(name, CHAMPION) is None
    tracker.set_alias(name, CHAMPION, version)
    tracker.set_version_tags(name, version, {"gate": "passed"})
    assert tracker.get_alias(name, CHAMPION) == version
    tracker.finish_run(run_id)


def test_promote_improve_reject_rollback(scratch: tuple[ObjectStore, str], tmp_path: Path) -> None:
    store, root = scratch
    put_signal_silver(store, root, n=200)
    tag = uuid.uuid4().hex[:8]
    cfg = SIGNAL_CONFIG.model_copy(
        update={"registry": RegistryConfig(model_name=f"it-{tag}", experiment=f"it-{tag}")}
    )
    tracker = MlflowTracker(get_settings().mlflow_tracking_uri)

    def ctx(config: TrainingConfig, sub: str) -> PipelineContext:
        return PipelineContext(
            data_store=store,
            model_store=store,
            tracker=tracker,
            cfg=config,
            stations=STATIONS,
            workdir=tmp_path / sub,
            root=root,
            git_sha="it",
        )

    weak = run_training_pipeline(ctx(cfg.model_copy(update={"lightgbm": WEAK_LIGHTGBM}), "1"))
    assert weak.promoted, weak.decision
    better = run_training_pipeline(ctx(cfg, "2"))
    assert better.promoted, better.decision
    same = run_training_pipeline(ctx(cfg, "3"))
    assert not same.promoted
    assert same.decision.failed == ["brier_vs_champion"]

    pointer = ObjectStorePointer(store, root)
    state = pointer.get()
    assert state is not None
    assert (state.champion_version, state.previous_version) == (better.version, weak.version)
    assert tracker.get_alias(cfg.registry.model_name, CHAMPION) == better.version

    back = rollback(store, pointer, tracker, model_name=cfg.registry.model_name, root=root)
    assert back.champion_version == weak.version
    assert tracker.get_alias(cfg.registry.model_name, CHAMPION) == weak.version
