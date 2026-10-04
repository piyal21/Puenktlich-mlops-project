"""`training_pipeline` steps (architecture §5.2), shared by `make train` and the Airflow DAG.

Steps hand off through one MLflow run: bundle files are logged under `bundle/`, and only ids
(snapshot id, run id, model version) travel between steps.
"""

import hashlib
import io
import json
import os
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.typing import NDArray
from pydantic import BaseModel

from dbdelay.config import Settings, get_settings
from dbdelay.data.schemas import validate_silver
from dbdelay.data.stations import Station, load_stations
from dbdelay.errors import ArtifactIntegrityError, DataValidationError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FeatureSpec, fit_spec
from dbdelay.logging import get_logger
from dbdelay.registry.artifacts import BUNDLE_FILES, build_manifest, load_bundle
from dbdelay.registry.pointer import ObjectStorePointer, PointerState
from dbdelay.registry.release import CHALLENGER, release_version
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.baseline import BaselineModel
from dbdelay.training.calibrate import IsotonicCalibrator
from dbdelay.training.config import TrainingConfig
from dbdelay.training.evaluate import (
    EvaluationReport,
    SplitReport,
    TrainingReport,
    evaluate_split,
    slice_frame,
)
from dbdelay.training.gate import GateDecision, evaluate_gate
from dbdelay.training.model_card import render_model_card
from dbdelay.training.run_baseline import BASELINE_DIR, fit_baseline
from dbdelay.training.split import (
    SNAPSHOT_FILE,
    SPLITS,
    SnapshotManifest,
    build_snapshot,
    load_snapshot_split,
    snapshot_prefix,
)
from dbdelay.training.tracking import MlflowTracker, Tracker
from dbdelay.training.train import (
    booster_from_text,
    feature_importance,
    predict_raw,
    train_lightgbm,
)

BUNDLE_DIR = "bundle/"
CONFIG_FILE = "training_config.json"
LOCK_FILE = Path("uv.lock")  # present in the repo checkout; the Airflow image installs via pip
# Library versions logged with every run (rules.md §5.4: the environment of the run).
TRACKED_PACKAGES: tuple[str, ...] = (
    "lightgbm",
    "scikit-learn",
    "numpy",
    "pandas",
    "pyarrow",
    "mlflow-skinny",
)
GATE_FILE = "gate.json"
GRID_FILE = "grid_scores.json"
SMOKE_FILE = "smoke/expected.json"
SMOKE_ROWS = 1000
SMOKE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class PipelineContext:
    data_store: ObjectStore
    model_store: ObjectStore
    tracker: Tracker
    cfg: TrainingConfig
    stations: tuple[Station, ...]
    workdir: Path
    root: str = ""
    git_sha: str = "unknown"


class PipelineResult(BaseModel):
    snapshot_id: str
    run_id: str
    version: str
    promoted: bool
    decision: GateDecision
    champion_version: str | None


def current_git_sha() -> str:
    """`GIT_SHA` (set in the Airflow image), else the checkout's short sha, else "unknown"."""
    env = os.environ.get("GIT_SHA")
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH, fixed argv
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def make_context(
    cfg: TrainingConfig,
    workdir: Path,
    *,
    settings: Settings | None = None,
    tracker: Tracker | None = None,
) -> PipelineContext:
    """Context from environment settings (MinIO buckets, MLflow server, station list)."""
    settings = settings or get_settings()
    client = make_s3_client(settings)
    return PipelineContext(
        data_store=ObjectStore(client, settings.data_bucket),
        model_store=ObjectStore(client, settings.models_bucket),
        tracker=tracker or MlflowTracker(settings.mlflow_tracking_uri),
        cfg=cfg,
        stations=tuple(load_stations(settings.stations_file)),
        workdir=workdir,
        git_sha=current_git_sha(),
    )


def environment_tags() -> dict[str, str]:
    """`version.<package>` per tracked library, plus `uv_lock_sha256` when `uv.lock` exists."""
    tags: dict[str, str] = {}
    for package in TRACKED_PACKAGES:
        try:
            tags[f"version.{package}"] = metadata.version(package)
        except metadata.PackageNotFoundError:
            tags[f"version.{package}"] = "missing"
    if LOCK_FILE.is_file():
        tags["uv_lock_sha256"] = hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest()
    return tags


@contextmanager
def _failing_run(ctx: PipelineContext, run_id: str) -> Iterator[None]:
    """Mark the MLflow run FAILED if a step raises, then re-raise."""
    try:
        yield
    except Exception:
        ctx.tracker.finish_run(run_id, failed=True)
        raise


def _frames(
    ctx: PipelineContext, snapshot_id: str, names: Sequence[str]
) -> dict[str, pd.DataFrame]:
    return {
        name: load_snapshot_split(ctx.data_store, snapshot_id, name, ctx.workdir, root=ctx.root)
        for name in names
    }


def _labels(frame: pd.DataFrame) -> NDArray[np.bool_]:
    return frame["is_late"].to_numpy(dtype=bool)


def _snapshot(ctx: PipelineContext, snapshot_id: str) -> SnapshotManifest:
    key = snapshot_prefix(snapshot_id, ctx.root) + SNAPSHOT_FILE
    return SnapshotManifest.model_validate_json(ctx.data_store.get_bytes(key))


def _json(data: object) -> bytes:
    return json.dumps(data, indent=2, sort_keys=True).encode()


def build_training_set(ctx: PipelineContext) -> SnapshotManifest:
    """Build (or reuse) the gold snapshot of the configured window."""
    return build_snapshot(ctx.data_store, ctx.cfg, ctx.workdir / "snapshot", root=ctx.root)


def validate_snapshot(ctx: PipelineContext, snapshot_id: str) -> None:
    """Silver contract + row counts + date ranges of every split against `snapshot.json`.

    Raises:
        DataValidationError: on any mismatch.
    """
    manifest = _snapshot(ctx, snapshot_id)
    for name, frame in _frames(ctx, snapshot_id, SPLITS).items():
        info = manifest.splits[name]
        if frame.empty or len(frame) != info.rows:
            raise DataValidationError(f"split {name}: {len(frame)} rows, manifest says {info.rows}")
        # Gold rows are labelled only, so parquet round-trips `is_late` as plain bool; the
        # silver contract types it as nullable boolean.
        validate_silver(frame.astype({"is_late": "boolean"}))
        days = frame["planned_departure_utc"].dt.date
        if days.min() < info.start or days.max() > info.end:
            raise DataValidationError(f"split {name}: rows outside {info.start}..{info.end}")


def train_baseline(ctx: PipelineContext, snapshot_id: str) -> EvaluationReport:
    """Late-rate baseline on this snapshot (Phase 3), saved next to it."""
    return fit_baseline(
        ctx.data_store, ctx.cfg, ctx.stations, snapshot_id, ctx.workdir / "baseline", root=ctx.root
    )


def train_model(ctx: PipelineContext, snapshot_id: str) -> str:
    """Fit spec (+ risk thresholds) and the LightGBM grid in a new MLflow run; return its id."""
    cfg = ctx.cfg
    manifest = _snapshot(ctx, snapshot_id)
    frames = _frames(ctx, snapshot_id, ("train", "valid"))
    spec = fit_spec(frames["train"], ctx.stations, cfg.features.min_count).model_copy(
        update={"risk_thresholds": cfg.risk_thresholds}
    )
    features = {name: build_features(frame, spec) for name, frame in frames.items()}
    run_id = ctx.tracker.start_run(
        cfg.registry.experiment,
        {
            "snapshot_id": snapshot_id,
            "git_sha": ctx.git_sha,
            "spec_hash": spec.spec_hash,
            "feature_version": str(spec.version),
            **environment_tags(),
        },
    )
    with _failing_run(ctx, run_id):
        ctx.tracker.log_bytes(run_id, CONFIG_FILE, _json(cfg.model_dump(mode="json")))
        source = f"s3://{ctx.data_store.bucket}/{snapshot_prefix(snapshot_id, ctx.root)}"
        ctx.tracker.log_snapshot(run_id, snapshot_id, manifest.content_hash, source)
        result = train_lightgbm(
            features["train"],
            _labels(frames["train"]),
            features["valid"],
            _labels(frames["valid"]),
            cfg.lightgbm,
            seed=cfg.seed,
        )
        ctx.tracker.log_params(
            run_id,
            {
                **result.params,
                "best_iteration": result.best_iteration,
                "train_rows": len(frames["train"]),
                "valid_rows": len(frames["valid"]),
                "window_start": manifest.window_start.isoformat(),
                "window_end": manifest.window_end.isoformat(),
            },
        )
        ctx.tracker.log_metrics(run_id, {"valid_logloss_raw": result.valid_logloss})
        model_text = result.model_text()
        ctx.tracker.log_bytes(run_id, BUNDLE_DIR + "model.txt", model_text.encode())
        ctx.tracker.log_bytes(run_id, BUNDLE_DIR + "feature_spec.json", spec.to_json().encode())
        ctx.tracker.log_bytes(run_id, GRID_FILE, _json([s.model_dump() for s in result.grid]))
        importance = feature_importance(booster_from_text(model_text))
        ctx.tracker.log_bytes(run_id, "feature_importance.json", _json(importance))
    return run_id


def _load_model(ctx: PipelineContext, run_id: str) -> tuple[lgb.Booster, FeatureSpec]:
    text = ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "model.txt").decode("utf-8")
    spec = FeatureSpec.from_json(ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "feature_spec.json"))
    return booster_from_text(text), spec


def calibrate_model(ctx: PipelineContext, snapshot_id: str, run_id: str) -> None:
    """Isotonic calibrator on the valid split's raw scores -> `bundle/calibrator.json`."""
    with _failing_run(ctx, run_id):
        booster, spec = _load_model(ctx, run_id)
        valid = _frames(ctx, snapshot_id, ("valid",))["valid"]
        calibrator = IsotonicCalibrator.fit(
            predict_raw(booster, build_features(valid, spec)), _labels(valid)
        )
        ctx.tracker.log_bytes(run_id, BUNDLE_DIR + "calibrator.json", calibrator.to_json().encode())


def _evaluate(
    ctx: PipelineContext,
    labels: NDArray[np.bool_],
    p_late: NDArray[np.float64],
    slices: pd.DataFrame,
) -> SplitReport:
    ev = ctx.cfg.evaluation
    return evaluate_split(
        labels, p_late, slices, ece_bins=ev.ece_bins, slice_min_rows=ev.slice_min_rows
    )


def _flat_metrics(prefix: str, split: SplitReport) -> dict[str, float]:
    return {
        f"{prefix}_{key}": float(value)
        for key, value in split.overall.model_dump().items()
        if key not in {"n", "base_rate"} and value is not None
    }


def _reference_sample(
    ctx: PipelineContext,
    features: pd.DataFrame,
    labels: NDArray[np.bool_],
    p_late: NDArray[np.float64],
) -> bytes:
    """Seeded train sample (features + label + p_late) as the drift reference."""
    n = min(len(features), ctx.cfg.release.reference_sample_rows)
    index = np.sort(np.random.default_rng(ctx.cfg.seed).choice(len(features), n, replace=False))
    sample = features.iloc[index].reset_index(drop=True)
    sample["is_late"] = labels[index]
    sample["p_late"] = p_late[index]
    buffer = io.BytesIO()
    sample.to_parquet(buffer, index=False, compression="zstd")
    return buffer.getvalue()


def _chosen_params(ctx: PipelineContext, run_id: str) -> dict[str, object]:
    grid = json.loads(ctx.tracker.load_bytes(run_id, GRID_FILE))
    best = min(grid, key=lambda score: score["valid_logloss"])
    return {**best["params"], "best_iteration": best["best_iteration"]}


def _baseline_predictions(
    ctx: PipelineContext, snapshot_id: str, test: pd.DataFrame
) -> NDArray[np.float64]:
    prefix = snapshot_prefix(snapshot_id, ctx.root) + BASELINE_DIR
    spec = FeatureSpec.from_json(ctx.data_store.get_bytes(prefix + "feature_spec.json"))
    baseline = BaselineModel.from_json(ctx.data_store.get_bytes(prefix + "baseline.json"))
    return baseline.predict(build_features(test, spec))


def evaluate_models(ctx: PipelineContext, snapshot_id: str, run_id: str) -> TrainingReport:
    """Challenger, baseline and champion on the same test rows; completes the run's bundle.

    Raises:
        ArtifactIntegrityError: if the pointer names a champion whose bundle does not verify.
    """
    cfg = ctx.cfg
    with _failing_run(ctx, run_id):
        booster, spec = _load_model(ctx, run_id)
        calibrator = IsotonicCalibrator.from_json(
            ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "calibrator.json")
        )
        frames = _frames(ctx, snapshot_id, SPLITS)
        labels = {name: _labels(frame) for name, frame in frames.items()}
        features = {name: build_features(frame, spec) for name, frame in frames.items()}
        p_late = {
            name: calibrator.apply(predict_raw(booster, feats)) for name, feats in features.items()
        }
        slices = slice_frame(features["test"])

        pointer = ObjectStorePointer(ctx.model_store, ctx.root).get()
        champion_version = pointer.champion_version if pointer else None
        champion_test = None
        if champion_version is not None:
            champion = load_bundle(ctx.model_store, champion_version, ctx.root)
            champion_p = champion.predict(frames["test"])
            champion_test = _evaluate(ctx, labels["test"], champion_p, slices)

        manifest = _snapshot(ctx, snapshot_id)
        baseline_p = _baseline_predictions(ctx, snapshot_id, frames["test"])
        valid_slices = slice_frame(features["valid"])
        report = TrainingReport(
            snapshot_id=snapshot_id,
            spec_hash=spec.spec_hash,
            git_sha=ctx.git_sha,
            trained_at=datetime.now(UTC),
            train_start=manifest.splits["train"].start,
            train_end=manifest.splits["train"].end,
            test_rows=len(frames["test"]),
            challenger_valid=_evaluate(ctx, labels["valid"], p_late["valid"], valid_slices),
            challenger_test=_evaluate(ctx, labels["test"], p_late["test"], slices),
            baseline_test=_evaluate(ctx, labels["test"], baseline_p, slices),
            champion_version=champion_version,
            champion_test=champion_test,
        )
        metrics = _flat_metrics("valid", report.challenger_valid)
        metrics |= _flat_metrics("test", report.challenger_test)
        metrics |= _flat_metrics("baseline_test", report.baseline_test)
        if report.champion_test is not None:
            metrics |= _flat_metrics("champion_test", report.champion_test)
        ctx.tracker.log_metrics(run_id, metrics)
        card = render_model_card(
            report,
            model_name=cfg.registry.model_name,
            params=_chosen_params(ctx, run_id),
            risk=cfg.risk_thresholds,
        )
        bundle_files = {
            "metrics.json": report.model_dump_json(indent=2).encode(),
            "model_card.md": card.encode(),
            "reference_sample.parquet": _reference_sample(
                ctx, features["train"], labels["train"], p_late["train"]
            ),
        }
        for name, data in bundle_files.items():
            ctx.tracker.log_bytes(run_id, BUNDLE_DIR + name, data)
        calibration = [b.model_dump() for b in report.challenger_test.calibration]
        ctx.tracker.log_bytes(run_id, "calibration_bins.json", _json(calibration))
        ctx.tracker.log_bytes(run_id, SMOKE_FILE, _json(p_late["test"][:SMOKE_ROWS].tolist()))
    return report


def register_model(ctx: PipelineContext, run_id: str) -> str:
    """Register `bundle/` as a new model version with alias @challenger; return the version."""
    name = ctx.cfg.registry.model_name
    with _failing_run(ctx, run_id):
        report = TrainingReport.model_validate_json(
            ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "metrics.json")
        )
        model_version = ctx.tracker.register_version(name, run_id, BUNDLE_DIR.rstrip("/"))
        ctx.tracker.set_version_tags(
            name, model_version, {"git_sha": report.git_sha, "snapshot_id": report.snapshot_id}
        )
        ctx.tracker.set_alias(name, CHALLENGER, model_version)
        ctx.tracker.set_tags(run_id, {"model_version": model_version})
    return model_version


def run_gate(ctx: PipelineContext, run_id: str, version: str) -> GateDecision:
    """Gate the challenger; record the decision on the run and the model version."""
    with _failing_run(ctx, run_id):
        report = TrainingReport.model_validate_json(
            ctx.tracker.load_bytes(run_id, BUNDLE_DIR + "metrics.json")
        )
        decision = evaluate_gate(report, ctx.cfg.gate)
        status = "passed" if decision.passed else "rejected"
        ctx.tracker.log_bytes(run_id, GATE_FILE, decision.model_dump_json(indent=2).encode())
        ctx.tracker.set_tags(run_id, {"gate": status})
        ctx.tracker.set_version_tags(ctx.cfg.registry.model_name, version, {"gate_result": status})
    return decision


def record_rejection(ctx: PipelineContext, run_id: str, version: str) -> None:
    """Rejected challenger: log why and close the run (the pipeline still succeeds)."""
    with _failing_run(ctx, run_id):
        decision = GateDecision.model_validate_json(ctx.tracker.load_bytes(run_id, GATE_FILE))
        get_logger("training").info(
            "challenger rejected",
            extra={
                "version": version,
                "failed": decision.failed,
                "champion": decision.champion_version,
            },
        )
    ctx.tracker.finish_run(run_id)


def release_model(ctx: PipelineContext, run_id: str, version: str) -> PointerState:
    """Export the run's bundle to `models/<version>/`, move the pointer and @champion."""
    with _failing_run(ctx, run_id):
        files = {name: ctx.tracker.load_bytes(run_id, BUNDLE_DIR + name) for name in BUNDLE_FILES}
        report = TrainingReport.model_validate_json(files["metrics.json"])
        manifest = build_manifest(
            files,
            model_name=ctx.cfg.registry.model_name,
            version=version,
            run_id=run_id,
            report=report,
        )
        return release_version(
            ctx.model_store,
            ObjectStorePointer(ctx.model_store, ctx.root),
            ctx.tracker,
            manifest=manifest,
            files=files,
            root=ctx.root,
        )


def smoke_test(ctx: PipelineContext, snapshot_id: str, run_id: str, version: str) -> None:
    """Released bundle (checksums verified) reproduces the evaluated test predictions.

    Raises:
        ArtifactIntegrityError: if predictions differ or leave [0, 1].
    """
    with _failing_run(ctx, run_id):
        bundle = load_bundle(ctx.model_store, version, ctx.root)
        expected = np.asarray(json.loads(ctx.tracker.load_bytes(run_id, SMOKE_FILE)), dtype=float)
        test = _frames(ctx, snapshot_id, ("test",))["test"].head(len(expected))
        got = bundle.predict(test)
        in_range = bool(np.all((got >= 0) & (got <= 1)))
        if not in_range or float(np.max(np.abs(got - expected))) > SMOKE_TOLERANCE:
            raise ArtifactIntegrityError(f"smoke test failed for models/{version}")
    ctx.tracker.finish_run(run_id)


def run_training_pipeline(ctx: PipelineContext) -> PipelineResult:
    """All steps in one process (`make train`); the DAG runs the same steps as tasks."""
    snapshot_id = build_training_set(ctx).snapshot_id
    validate_snapshot(ctx, snapshot_id)
    train_baseline(ctx, snapshot_id)
    run_id = train_model(ctx, snapshot_id)
    calibrate_model(ctx, snapshot_id, run_id)
    evaluate_models(ctx, snapshot_id, run_id)
    version = register_model(ctx, run_id)
    decision = run_gate(ctx, run_id, version)
    if decision.passed:
        release_model(ctx, run_id, version)
        smoke_test(ctx, snapshot_id, run_id, version)
    else:
        record_rejection(ctx, run_id, version)
    pointer = ObjectStorePointer(ctx.model_store, ctx.root).get()
    return PipelineResult(
        snapshot_id=snapshot_id,
        run_id=run_id,
        version=version,
        promoted=decision.passed,
        decision=decision,
        champion_version=pointer.champion_version if pointer else None,
    )
