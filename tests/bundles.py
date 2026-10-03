"""A small, valid model bundle for registry/pipeline unit tests (no storage, no MLflow)."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from dbdelay.data.stations import load_stations
from dbdelay.features.build import build_features
from dbdelay.features.spec import RiskThresholds, fit_spec
from dbdelay.registry.artifacts import Manifest, build_manifest
from dbdelay.training.calibrate import IsotonicCalibrator
from dbdelay.training.evaluate import Metrics, SplitReport, TrainingReport
from dbdelay.training.train import booster_from_text, predict_raw, train_lightgbm
from tests.builders import SIGNAL_CONFIG, signal_frames

STATIONS_FILE = Path(__file__).resolve().parents[1] / "configs" / "stations.yaml"


def _split(brier: float, auc: float) -> SplitReport:
    return SplitReport(
        overall=Metrics(n=10, base_rate=0.3, brier=brier, roc_auc=auc), calibration=[], slices={}
    )


def make_bundle_files() -> dict[str, bytes]:
    """The six bundle files of a model trained on the signal data."""
    frames = signal_frames()
    spec = fit_spec(frames["train"], load_stations(STATIONS_FILE), 1).model_copy(
        update={"risk_thresholds": RiskThresholds(medium=0.2, high=0.45)}
    )
    feats = {k: build_features(v, spec) for k, v in frames.items()}
    labels = {k: v["is_late"].to_numpy(dtype=bool) for k, v in frames.items()}
    result = train_lightgbm(
        feats["train"],
        labels["train"],
        feats["valid"],
        labels["valid"],
        SIGNAL_CONFIG.lightgbm,
        seed=42,
    )
    raw_valid = predict_raw(booster_from_text(result.model_text()), feats["valid"])
    report = TrainingReport(
        snapshot_id="2026-03-31_abcdef12",
        spec_hash=spec.spec_hash,
        git_sha="abc1234",
        trained_at=datetime(2026, 10, 4, tzinfo=UTC),
        train_start=date(2026, 3, 1),
        train_end=date(2026, 3, 17),
        test_rows=10,
        challenger_valid=_split(0.14, 0.8),
        challenger_test=_split(0.14, 0.8),
        baseline_test=_split(0.16, 0.7),
    )
    return {
        "model.txt": result.model_text().encode(),
        "calibrator.json": IsotonicCalibrator.fit(raw_valid, labels["valid"]).to_json().encode(),
        "feature_spec.json": spec.to_json().encode(),
        "metrics.json": report.model_dump_json().encode(),
        "model_card.md": b"# card\n",
        "reference_sample.parquet": b"PAR1",
    }


def bundle_manifest(
    files: Mapping[str, bytes], version: str = "1", model_name: str = "m"
) -> Manifest:
    report = TrainingReport.model_validate_json(files["metrics.json"])
    return build_manifest(files, model_name=model_name, version=version, run_id="r1", report=report)
