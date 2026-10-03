import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.errors import ArtifactIntegrityError, DataValidationError
from dbdelay.registry.artifacts import BUNDLE_FILES, models_prefix
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.registry.release import CHALLENGER, CHAMPION
from dbdelay.storage import ObjectStore
from dbdelay.training import pipeline, run_train
from dbdelay.training.config import TrainingConfig
from dbdelay.training.pipeline import PipelineContext, run_training_pipeline
from dbdelay.training.split import SNAPSHOT_FILE, snapshot_prefix
from tests.builders import SIGNAL_CONFIG, WEAK_LIGHTGBM, put_signal_silver
from tests.fakes import FakeTracker

REPO = Path(__file__).resolve().parents[2]
STATIONS = tuple(load_stations(REPO / "configs" / "stations.yaml"))
MODEL = SIGNAL_CONFIG.registry.model_name


def _ctx(
    store: ObjectStore, tracker: FakeTracker, tmp: Path, cfg: TrainingConfig = SIGNAL_CONFIG
) -> PipelineContext:
    return PipelineContext(
        data_store=store,
        model_store=store,
        tracker=tracker,
        cfg=cfg,
        stations=STATIONS,
        workdir=tmp,
        git_sha="abc1234",
    )


@pytest.fixture
def signal_store(s3_store: ObjectStore) -> ObjectStore:
    put_signal_silver(s3_store)
    return s3_store


def test_first_run_promotes_second_identical_run_is_rejected(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    tracker = FakeTracker()
    first = run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "a"))
    assert first.promoted, first.decision
    assert first.version == "1"
    assert first.champion_version == "1"
    assert tracker.get_alias(MODEL, CHAMPION) == "1"
    run = tracker.runs[first.run_id]
    assert run.status == "FINISHED"
    assert run.tags["snapshot_id"] == first.snapshot_id
    assert run.tags["git_sha"] == "abc1234"
    assert run.tags["gate"] == "passed"
    assert run.inputs[0][0] == first.snapshot_id
    assert "test_brier" in run.metrics
    for name in BUNDLE_FILES:
        assert signal_store.exists(models_prefix("1") + name)

    second = run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "b"))
    assert not second.promoted
    assert second.version == "2"
    assert second.decision.failed == ["brier_vs_champion"]
    assert second.champion_version == "1"
    assert tracker.get_alias(MODEL, CHALLENGER) == "2"
    assert tracker.get_alias(MODEL, CHAMPION) == "1"
    assert tracker.version_tags[(MODEL, "2")]["gate"] == "rejected"
    assert tracker.runs[second.run_id].status == "FINISHED"
    assert not signal_store.exists(models_prefix("2") + "manifest.json")


def test_weak_champion_is_replaced_by_better_model(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    tracker = FakeTracker()
    weak = SIGNAL_CONFIG.model_copy(update={"lightgbm": WEAK_LIGHTGBM})
    first = run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "a", weak))
    assert first.promoted, first.decision
    better = run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "b"))
    assert better.promoted, better.decision
    state = ObjectStorePointer(signal_store).get()
    assert state is not None
    assert (state.champion_version, state.previous_version) == ("2", "1")


def test_bundle_files_are_consistent(signal_store: ObjectStore, tmp_path: Path) -> None:
    tracker = FakeTracker()
    result = run_training_pipeline(_ctx(signal_store, tracker, tmp_path))
    metrics = json.loads(tracker.load_bytes(result.run_id, "bundle/metrics.json"))
    assert metrics["snapshot_id"] == result.snapshot_id
    assert metrics["champion_version"] is None
    spec = json.loads(tracker.load_bytes(result.run_id, "bundle/feature_spec.json"))
    assert spec["risk_thresholds"] == {"medium": 0.2, "high": 0.45}
    expected = json.loads(tracker.load_bytes(result.run_id, pipeline.SMOKE_FILE))
    assert 0 < len(expected) <= pipeline.SMOKE_ROWS
    gate = json.loads(tracker.load_bytes(result.run_id, pipeline.GATE_FILE))
    assert gate["passed"] is True
    card = tracker.load_bytes(result.run_id, "bundle/model_card.md").decode()
    assert "no champion yet" in card
    importance = json.loads(tracker.load_bytes(result.run_id, "feature_importance.json"))
    assert set(importance) == {"gain", "split"}


def test_tampered_champion_fails_closed(signal_store: ObjectStore, tmp_path: Path) -> None:
    tracker = FakeTracker()
    run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "a"))
    signal_store.put_bytes(models_prefix("1") + "model.txt", b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        run_training_pipeline(_ctx(signal_store, tracker, tmp_path / "b"))
    assert tracker.runs["run2"].status == "FAILED"


def test_pointer_to_missing_champion_fails_closed(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    ObjectStorePointer(signal_store).set("9", None)
    tracker = FakeTracker()
    with pytest.raises(ArtifactIntegrityError, match="manifest missing"):
        run_training_pipeline(_ctx(signal_store, tracker, tmp_path))
    assert tracker.runs["run1"].status == "FAILED"


def test_validate_snapshot_rejects_row_count_mismatch(
    signal_store: ObjectStore, tmp_path: Path
) -> None:
    ctx = _ctx(signal_store, FakeTracker(), tmp_path)
    manifest = pipeline.build_training_set(ctx)
    pipeline.validate_snapshot(ctx, manifest.snapshot_id)
    test_info = manifest.splits["test"].model_copy(update={"rows": 1})
    broken = manifest.model_copy(update={"splits": {**manifest.splits, "test": test_info}})
    key = snapshot_prefix(manifest.snapshot_id) + SNAPSHOT_FILE
    signal_store.put_bytes(key, broken.model_dump_json().encode())
    with pytest.raises(DataValidationError, match="test"):
        pipeline.validate_snapshot(ctx, manifest.snapshot_id)


def test_smoke_test_detects_prediction_drift(signal_store: ObjectStore, tmp_path: Path) -> None:
    tracker = FakeTracker()
    result = run_training_pipeline(_ctx(signal_store, tracker, tmp_path))
    expected = np.array(json.loads(tracker.load_bytes(result.run_id, pipeline.SMOKE_FILE)))
    shifted = json.dumps((expected + 0.01).tolist()).encode()
    tracker.log_bytes(result.run_id, pipeline.SMOKE_FILE, shifted)
    with pytest.raises(ArtifactIntegrityError, match="smoke"):
        pipeline.smoke_test(
            _ctx(signal_store, tracker, tmp_path), result.snapshot_id, result.run_id, "1"
        )


def test_run_train_main(
    signal_store: ObjectStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_BUCKET", signal_store.bucket)
    monkeypatch.setenv("MODELS_BUCKET", signal_store.bucket)
    monkeypatch.setenv("STATIONS_FILE", str(REPO / "configs" / "stations.yaml"))
    get_settings.cache_clear()
    tracker = FakeTracker()
    monkeypatch.setattr(run_train, "MlflowTracker", lambda uri: tracker)
    config = tmp_path / "training.yaml"
    config.write_text(yaml.safe_dump(SIGNAL_CONFIG.model_dump(mode="json")), encoding="utf-8")
    assert run_train.main(["--config", str(config)]) == 0
    assert tracker.get_alias(MODEL, CHAMPION) == "1"
