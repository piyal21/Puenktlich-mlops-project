from datetime import date

import numpy as np
import pytest

from dbdelay.errors import ArtifactIntegrityError
from dbdelay.features.spec import RiskThresholds
from dbdelay.registry.artifacts import (
    BUNDLE_FILES,
    MANIFEST_FILE,
    load_bundle,
    models_prefix,
    sha256_ref,
    write_bundle,
)
from dbdelay.storage import ObjectStore
from tests.builders import signal_frames
from tests.bundles import bundle_manifest


def test_manifest_hashes_every_file(bundle_files: dict[str, bytes]) -> None:
    manifest = bundle_manifest(bundle_files)
    assert set(manifest.files) == set(BUNDLE_FILES)
    assert manifest.files["model.txt"] == sha256_ref(bundle_files["model.txt"])
    assert manifest.metrics == {"test_brier": 0.14, "test_auc": 0.8, "baseline_brier": 0.16}
    assert manifest.train_window == {"start": date(2026, 3, 1), "end": date(2026, 3, 17)}
    assert manifest.data_snapshot_id == "2026-03-31_abcdef12"
    assert manifest.git_sha == "abc1234"


def test_manifest_rejects_missing_or_extra_files(bundle_files: dict[str, bytes]) -> None:
    with pytest.raises(ArtifactIntegrityError):
        bundle_manifest({k: v for k, v in bundle_files.items() if k != "model.txt"})
    with pytest.raises(ArtifactIntegrityError):
        bundle_manifest(bundle_files | {"extra.bin": b""})


def test_write_then_load_predicts(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    write_bundle(s3_store, bundle_manifest(bundle_files), bundle_files)
    bundle = load_bundle(s3_store, "1")
    test = signal_frames()["test"]
    p = bundle.predict(test)
    assert p.shape == (len(test),)
    assert np.all((p >= 0) & (p <= 1))
    assert bundle.spec.risk_thresholds == RiskThresholds(medium=0.2, high=0.45)


def test_tampered_file_fails_closed(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    write_bundle(s3_store, bundle_manifest(bundle_files), bundle_files)
    s3_store.put_bytes(models_prefix("1") + "model.txt", b"tree\n")
    with pytest.raises(ArtifactIntegrityError, match=r"model\.txt"):
        load_bundle(s3_store, "1")


def test_missing_file_fails_closed(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    manifest = bundle_manifest(bundle_files)
    s3_store.put_bytes(models_prefix("1") + MANIFEST_FILE, manifest.model_dump_json().encode())
    with pytest.raises(ArtifactIntegrityError, match="missing"):
        load_bundle(s3_store, "1")


def test_unlisted_file_fails_closed(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    write_bundle(s3_store, bundle_manifest(bundle_files), bundle_files)
    s3_store.put_bytes(models_prefix("1") + "surprise.txt", b"x")
    with pytest.raises(ArtifactIntegrityError, match="unlisted"):
        load_bundle(s3_store, "1")


def test_missing_version_fails_closed(s3_store: ObjectStore) -> None:
    with pytest.raises(ArtifactIntegrityError, match="manifest missing"):
        load_bundle(s3_store, "7")


def test_invalid_manifest_fails_closed(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(models_prefix("1") + MANIFEST_FILE, b"{}")
    with pytest.raises(ArtifactIntegrityError, match="manifest invalid"):
        load_bundle(s3_store, "1")


def test_version_mismatch_fails_closed(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    manifest = bundle_manifest(bundle_files, version="2")
    write_bundle(s3_store, manifest, bundle_files)
    s3_store.put_bytes(models_prefix("3") + MANIFEST_FILE, manifest.model_dump_json().encode())
    with pytest.raises(ArtifactIntegrityError, match="version"):
        load_bundle(s3_store, "3")


def test_unparseable_model_fails_closed(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    broken = bundle_files | {"model.txt": b"not a model"}
    write_bundle(s3_store, bundle_manifest(broken), broken)
    with pytest.raises(ArtifactIntegrityError, match="parse"):
        load_bundle(s3_store, "1")
