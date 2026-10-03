"""Release a model version (upload → verify → pointer → @champion) and roll back (arch. §6)."""

import argparse
import sys
from collections.abc import Mapping, Sequence

from dbdelay.config import get_settings
from dbdelay.errors import ArtifactIntegrityError, ModelNotAvailableError
from dbdelay.registry.artifacts import (
    MANIFEST_FILE,
    Manifest,
    load_bundle,
    models_prefix,
    read_bundle_files,
    write_bundle,
)
from dbdelay.registry.pointer import ModelPointer, ObjectStorePointer, PointerState
from dbdelay.storage import ObjectStore, make_s3_client
from dbdelay.training.config import load_training_config
from dbdelay.training.tracking import MlflowTracker, Tracker

CHAMPION = "champion"
CHALLENGER = "challenger"


def release_version(  # noqa: PLR0913 - store, pointer, tracker + bundle + root
    store: ObjectStore,
    pointer: ModelPointer,
    tracker: Tracker,
    *,
    manifest: Manifest,
    files: Mapping[str, bytes],
    root: str = "",
) -> PointerState:
    """Make ``manifest.version`` the champion. Safe to re-run after a crash.

    The pointer is the source of truth for serving; the MLflow alias follows it.

    Raises:
        ArtifactIntegrityError: if ``models/<version>/`` exists with other files, or the
            uploaded bundle does not verify.
    """
    version = manifest.version
    if store.exists(models_prefix(version, root) + MANIFEST_FILE):
        existing, _ = read_bundle_files(store, version, root)
        if existing.files != manifest.files:
            raise ArtifactIntegrityError(f"models/{version} exists with different files")
    else:
        write_bundle(store, manifest, files, root)
    load_bundle(store, version, root)  # verify exactly what serving will load
    current = pointer.get()
    if current is not None and current.champion_version == version:
        state = current
    else:
        state = pointer.set(version, current.champion_version if current else None)
    tracker.set_alias(manifest.model_name, CHAMPION, version)
    return state


def rollback(
    store: ObjectStore,
    pointer: ModelPointer,
    tracker: Tracker,
    *,
    model_name: str,
    root: str = "",
) -> PointerState:
    """Swap champion and previous (a second rollback rolls forward again).

    Raises:
        ModelNotAvailableError: if there is no previous version.
        ArtifactIntegrityError: if the previous bundle does not verify (pointer unchanged).
    """
    current = pointer.get()
    if current is None or current.previous_version is None:
        raise ModelNotAvailableError("no previous model version to roll back to")
    load_bundle(store, current.previous_version, root)
    state = pointer.set(current.previous_version, current.champion_version)
    tracker.set_alias(model_name, CHAMPION, state.champion_version)
    return state


def rollback_main(argv: Sequence[str] | None = None, *, tracker: Tracker | None = None) -> int:
    """CLI for `make rollback`: champion ↔ previous in the pointer and the MLflow alias."""
    parser = argparse.ArgumentParser(description="Point the champion back at the previous version.")
    parser.parse_args(argv)
    settings = get_settings()
    cfg = load_training_config(settings.training_config_file)
    store = ObjectStore(make_s3_client(settings), settings.models_bucket)
    state = rollback(
        store,
        ObjectStorePointer(store),
        tracker or MlflowTracker(settings.mlflow_tracking_uri),
        model_name=cfg.registry.model_name,
    )
    sys.stdout.write(f"champion {state.champion_version} (was {state.previous_version})\n")
    return 0
