"""Model bundle contract: `models/<version>/` + `manifest.json` with SHA-256 (architecture §6).

Loaders verify every checksum and fail closed.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import lightgbm as lgb
import numpy as np
import pandas as pd
from lightgbm.basic import LightGBMError
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.errors import ArtifactIntegrityError, DataValidationError, NotFoundError
from dbdelay.features.build import build_features
from dbdelay.features.spec import FeatureSpec
from dbdelay.storage import ObjectStore
from dbdelay.training.calibrate import IsotonicCalibrator
from dbdelay.training.report import TrainingReport
from dbdelay.training.train import booster_from_text, predict_raw

MANIFEST_FILE = "manifest.json"
BUNDLE_FILES: tuple[str, ...] = (
    "model.txt",
    "calibrator.json",
    "feature_spec.json",
    "metrics.json",
    "model_card.md",
    "reference_sample.parquet",
)
CONTENT_TYPES = {
    "model.txt": "text/plain",
    "calibrator.json": "application/json",
    "feature_spec.json": "application/json",
    "metrics.json": "application/json",
    "model_card.md": "text/markdown",
    "reference_sample.parquet": "application/octet-stream",
}


def models_prefix(version: str, root: str = "") -> str:
    return f"{root}models/{version}/"


def sha256_ref(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Manifest(BaseModel):
    """`manifest.json` (schema version 1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    model_name: str
    version: str
    mlflow_run_id: str
    git_sha: str
    data_snapshot_id: str
    trained_at: datetime
    train_window: dict[str, date]
    files: dict[str, str]
    metrics: dict[str, float | None]


def _check_names(names: set[str]) -> None:
    missing = sorted(set(BUNDLE_FILES) - names)
    extra = sorted(names - set(BUNDLE_FILES))
    if missing or extra:
        raise ArtifactIntegrityError(f"bundle files: missing {missing}, unexpected {extra}")


def build_manifest(
    files: Mapping[str, bytes],
    *,
    model_name: str,
    version: str,
    run_id: str,
    report: TrainingReport,
) -> Manifest:
    """Manifest for exactly ``BUNDLE_FILES``.

    Raises:
        ArtifactIntegrityError: if a bundle file is missing or an unexpected one is given.
    """
    _check_names(set(files))
    return Manifest(
        model_name=model_name,
        version=version,
        mlflow_run_id=run_id,
        git_sha=report.git_sha,
        data_snapshot_id=report.snapshot_id,
        trained_at=report.trained_at,
        train_window={"start": report.train_start, "end": report.train_end},
        files={name: sha256_ref(files[name]) for name in BUNDLE_FILES},
        metrics={
            "test_brier": report.challenger_test.overall.brier,
            "test_auc": report.challenger_test.overall.roc_auc,
            "baseline_brier": report.baseline_test.overall.brier,
        },
    )


def write_bundle(
    store: ObjectStore, manifest: Manifest, files: Mapping[str, bytes], root: str = ""
) -> None:
    """Upload the bundle files, then the manifest last (a manifest means a complete bundle)."""
    prefix = models_prefix(manifest.version, root)
    for name in BUNDLE_FILES:
        store.put_bytes(prefix + name, files[name], CONTENT_TYPES[name])
    store.put_bytes(
        prefix + MANIFEST_FILE, manifest.model_dump_json(indent=2).encode(), "application/json"
    )


def read_bundle_files(
    store: ObjectStore, version: str, root: str = ""
) -> tuple[Manifest, dict[str, bytes]]:
    """Download and verify a bundle.

    Raises:
        ArtifactIntegrityError: missing/invalid manifest, version mismatch, missing or
            unlisted file, or checksum mismatch.
    """
    prefix = models_prefix(version, root)
    try:
        manifest = Manifest.model_validate_json(store.get_bytes(prefix + MANIFEST_FILE))
    except NotFoundError:
        raise ArtifactIntegrityError(f"models/{version}: manifest missing") from None
    except ValidationError:
        raise ArtifactIntegrityError(f"models/{version}: manifest invalid") from None
    if manifest.version != version:
        raise ArtifactIntegrityError(
            f"models/{version}: manifest is for version {manifest.version}"
        )
    _check_names(set(manifest.files))
    present = {key[len(prefix) :] for key in store.iter_keys(prefix)}
    unlisted = sorted(present - set(manifest.files) - {MANIFEST_FILE})
    if unlisted:
        raise ArtifactIntegrityError(f"models/{version}: unlisted files {unlisted}")
    files: dict[str, bytes] = {}
    for name, expected in manifest.files.items():
        try:
            data = store.get_bytes(prefix + name)
        except NotFoundError:
            raise ArtifactIntegrityError(f"models/{version}: {name} missing") from None
        if sha256_ref(data) != expected:
            raise ArtifactIntegrityError(f"models/{version}: checksum mismatch for {name}")
        files[name] = data
    return manifest, files


@dataclass(frozen=True)
class ModelBundle:
    manifest: Manifest
    booster: lgb.Booster
    calibrator: IsotonicCalibrator
    spec: FeatureSpec
    report: TrainingReport

    def predict(self, df: pd.DataFrame) -> NDArray[np.float64]:
        """Calibrated ``p_late`` for silver-shaped rows (features built with this bundle's spec)."""
        return self.calibrator.apply(predict_raw(self.booster, build_features(df, self.spec)))


def parse_bundle(manifest: Manifest, files: Mapping[str, bytes]) -> ModelBundle:
    """Parse verified bundle files.

    Raises:
        ArtifactIntegrityError: if a file cannot be parsed.
    """
    try:
        return ModelBundle(
            manifest=manifest,
            booster=booster_from_text(files["model.txt"].decode("utf-8")),
            calibrator=IsotonicCalibrator.from_json(files["calibrator.json"]),
            spec=FeatureSpec.from_json(files["feature_spec.json"]),
            report=TrainingReport.model_validate_json(files["metrics.json"]),
        )
    except (LightGBMError, DataValidationError, UnicodeDecodeError, ValidationError) as exc:
        raise ArtifactIntegrityError(
            f"models/{manifest.version}: cannot parse bundle files"
        ) from exc


def load_bundle(store: ObjectStore, version: str, root: str = "") -> ModelBundle:
    """Verified, parsed bundle of one model version (fails closed)."""
    manifest, files = read_bundle_files(store, version, root)
    return parse_bundle(manifest, files)
