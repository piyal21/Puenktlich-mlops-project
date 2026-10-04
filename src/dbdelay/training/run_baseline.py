"""`make baseline`: snapshot → feature spec → late-rate baseline → metrics (Phase 3)."""

import argparse
import tempfile
from collections.abc import Sequence
from pathlib import Path

from dbdelay.config import get_settings
from dbdelay.data.stations import Station, load_stations
from dbdelay.features.build import build_features
from dbdelay.features.spec import fit_spec
from dbdelay.logging import get_logger
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.baseline import BaselineModel
from dbdelay.training.config import TrainingConfig, load_training_config
from dbdelay.training.evaluate import EvaluationReport, evaluate_split, slice_frame
from dbdelay.training.split import SPLITS, build_snapshot, load_snapshot_split, snapshot_prefix

DEFAULT_CONFIG = Path("configs/training.yaml")
EVAL_SPLITS: tuple[str, ...] = ("valid", "test")
BASELINE_DIR = "baseline/"


def fit_baseline(  # noqa: PLR0913 - store, cfg, stations, snapshot, workdir, root
    store: ObjectStore,
    cfg: TrainingConfig,
    stations: Sequence[Station],
    snapshot_id: str,
    workdir: Path,
    *,
    root: str = "",
) -> EvaluationReport:
    """Fit spec + baseline on an existing snapshot's train split, evaluate valid and test, and
    write `baseline/{feature_spec,baseline,metrics}.json` next to the snapshot."""
    workdir.mkdir(parents=True, exist_ok=True)
    frames = {
        name: load_snapshot_split(store, snapshot_id, name, workdir, root=root) for name in SPLITS
    }
    spec = fit_spec(frames["train"], stations, cfg.features.min_count)
    features = {name: build_features(frame, spec) for name, frame in frames.items()}
    labels = {name: frame["is_late"].to_numpy(dtype=bool) for name, frame in frames.items()}
    model = BaselineModel.fit(features["train"], labels["train"], cfg.baseline.min_count)
    report = EvaluationReport(
        snapshot_id=snapshot_id,
        spec_hash=spec.spec_hash,
        config=cfg.model_dump(mode="json"),
        splits={
            name: evaluate_split(
                labels[name],
                model.predict(features[name]),
                slice_frame(features[name]),
                ece_bins=cfg.evaluation.ece_bins,
                slice_min_rows=cfg.evaluation.slice_min_rows,
            )
            for name in EVAL_SPLITS
        },
    )
    prefix = snapshot_prefix(snapshot_id, root) + BASELINE_DIR
    store.put_bytes(prefix + "feature_spec.json", spec.to_json().encode(), "application/json")
    store.put_bytes(prefix + "baseline.json", model.to_json().encode(), "application/json")
    store.put_bytes(
        prefix + "metrics.json", report.model_dump_json(indent=2).encode(), "application/json"
    )
    return report


def run_baseline(
    store: ObjectStore,
    cfg: TrainingConfig,
    stations: Sequence[Station],
    workdir: Path,
    *,
    root: str = "",
) -> EvaluationReport:
    """Build or reuse the snapshot, fit spec + baseline on train, evaluate valid and test."""
    manifest = build_snapshot(store, cfg, workdir, root=root)
    return fit_baseline(store, cfg, stations, manifest.snapshot_id, workdir, root=root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the training snapshot and score the baseline."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    settings = get_settings()
    cfg = load_training_config(args.config)
    stations = load_stations(settings.stations_file)
    store = ObjectStore(make_s3_client(settings), settings.data_bucket)
    log = get_logger("training")
    with tempfile.TemporaryDirectory() as tmp:
        report = run_baseline(store, cfg, stations, Path(tmp))
    for name, split in report.splits.items():
        log.info(
            "baseline metrics",
            extra={"snapshot_id": report.snapshot_id, "split": name, **split.overall.model_dump()},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
