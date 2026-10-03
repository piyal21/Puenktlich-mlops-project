"""`make train`: snapshot → train → calibrate → evaluate → register → gate → release (Phase 4)."""

import argparse
import tempfile
from collections.abc import Sequence
from pathlib import Path

from dbdelay.config import get_settings
from dbdelay.logging import get_logger
from dbdelay.training.config import load_training_config
from dbdelay.training.pipeline import make_context, run_training_pipeline
from dbdelay.training.tracking import MlflowTracker


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the training pipeline in-process.")
    parser.add_argument("--config", type=Path, default=settings.training_config_file)
    args = parser.parse_args(argv)
    cfg = load_training_config(args.config)
    with tempfile.TemporaryDirectory() as tmp:
        ctx = make_context(
            cfg, Path(tmp), settings=settings, tracker=MlflowTracker(settings.mlflow_tracking_uri)
        )
        result = run_training_pipeline(ctx)
    get_logger("training").info(
        "training pipeline finished",
        extra={
            "snapshot_id": result.snapshot_id,
            "run_id": result.run_id,
            "version": result.version,
            "promoted": result.promoted,
            "failed_checks": result.decision.failed,
            "champion_version": result.champion_version,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
