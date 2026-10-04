from pathlib import Path

import pytest
import yaml

from dbdelay.errors import ArtifactIntegrityError, ModelNotAvailableError
from dbdelay.registry.artifacts import models_prefix
from dbdelay.registry.pointer import POINTER_KEY, ObjectStorePointer, PointerState
from dbdelay.registry.release import CHAMPION, release_version, rollback, rollback_main
from dbdelay.storage import ObjectStore
from tests.builders import MARCH_CONFIG
from tests.bundles import bundle_manifest
from tests.fakes import FakeTracker

Files = dict[str, bytes]


def _release(store: ObjectStore, files: Files, tracker: FakeTracker, version: str) -> PointerState:
    return release_version(
        store,
        ObjectStorePointer(store),
        tracker,
        manifest=bundle_manifest(files, version),
        files=files,
    )


def test_pointer_absent_then_set(s3_store: ObjectStore) -> None:
    pointer = ObjectStorePointer(s3_store)
    assert pointer.get() is None
    state = pointer.set("2", "1")
    assert pointer.get() == state
    assert (state.champion_version, state.previous_version) == ("2", "1")


def test_corrupt_pointer_raises(s3_store: ObjectStore) -> None:
    s3_store.put_bytes(POINTER_KEY, b"{}")
    with pytest.raises(ArtifactIntegrityError):
        ObjectStorePointer(s3_store).get()


def test_release_sets_pointer_and_alias(s3_store: ObjectStore, bundle_files: Files) -> None:
    tracker = FakeTracker()
    first = _release(s3_store, bundle_files, tracker, "1")
    assert (first.champion_version, first.previous_version) == ("1", None)
    second = _release(s3_store, bundle_files, tracker, "2")
    assert (second.champion_version, second.previous_version) == ("2", "1")
    assert tracker.get_alias("m", CHAMPION) == "2"
    assert s3_store.exists(models_prefix("2") + "manifest.json")


def test_release_rerun_is_idempotent(s3_store: ObjectStore, bundle_files: Files) -> None:
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    tracker.aliases.clear()  # crash after the pointer write, before the alias
    again = _release(s3_store, bundle_files, tracker, "2")
    assert (again.champion_version, again.previous_version) == ("2", "1")
    assert tracker.get_alias("m", CHAMPION) == "2"


def test_release_refuses_different_existing_bundle(
    s3_store: ObjectStore, bundle_files: Files
) -> None:
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    changed = bundle_files | {"model_card.md": b"# other\n"}
    with pytest.raises(ArtifactIntegrityError, match="different"):
        _release(s3_store, changed, tracker, "1")


def test_rollback_swaps_back_and_moves_alias(s3_store: ObjectStore, bundle_files: Files) -> None:
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    state = rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")
    assert (state.champion_version, state.previous_version) == ("1", "2")
    assert tracker.get_alias("m", CHAMPION) == "1"


def test_rollback_without_previous_raises(s3_store: ObjectStore, bundle_files: Files) -> None:
    tracker = FakeTracker()
    with pytest.raises(ModelNotAvailableError):
        rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")
    _release(s3_store, bundle_files, tracker, "1")
    with pytest.raises(ModelNotAvailableError):
        rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")


def test_rollback_refuses_broken_previous(s3_store: ObjectStore, bundle_files: Files) -> None:
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    s3_store.put_bytes(models_prefix("1") + "model.txt", b"x")
    with pytest.raises(ArtifactIntegrityError):
        rollback(s3_store, ObjectStorePointer(s3_store), tracker, model_name="m")
    state = ObjectStorePointer(s3_store).get()
    assert state is not None
    assert state.champion_version == "2"


def test_rollback_main_prints_versions(
    s3_store: ObjectStore,
    bundle_files: Files,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = Path("training.yaml")
    model = MARCH_CONFIG.model_copy(
        update={"registry": MARCH_CONFIG.registry.model_copy(update={"model_name": "m"})}
    )
    config.write_text(yaml.safe_dump(model.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("MODELS_BUCKET", s3_store.bucket)
    monkeypatch.setenv("TRAINING_CONFIG_FILE", str(config))
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    _release(s3_store, bundle_files, tracker, "2")
    assert rollback_main([], tracker=tracker) == 0
    assert "champion 1 (was 2)" in capsys.readouterr().out
    assert tracker.get_alias("m", CHAMPION) == "1"


def test_rollback_main_without_previous_prints_reason(
    s3_store: ObjectStore,
    bundle_files: Files,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = Path("training.yaml")
    config.write_text(yaml.safe_dump(MARCH_CONFIG.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("MODELS_BUCKET", s3_store.bucket)
    monkeypatch.setenv("TRAINING_CONFIG_FILE", str(config))
    tracker = FakeTracker()
    _release(s3_store, bundle_files, tracker, "1")
    assert rollback_main([], tracker=tracker) == 1
    assert "rollback refused: no previous model version" in capsys.readouterr().err
    state = ObjectStorePointer(s3_store).get()
    assert state is not None
    assert state.champion_version == "1"
