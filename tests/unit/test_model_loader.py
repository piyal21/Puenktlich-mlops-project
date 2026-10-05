import pytest

from dbdelay.errors import ExternalServiceError, ModelNotAvailableError
from dbdelay.registry import artifacts
from dbdelay.registry.artifacts import models_prefix, write_bundle
from dbdelay.registry.pointer import ObjectStorePointer, PointerState
from dbdelay.serving import model_loader
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.storage import ObjectStore
from tests.bundles import bundle_manifest


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FlakyPointer:
    """Pointer whose storage can be switched off."""

    def __init__(self, inner: ObjectStorePointer) -> None:
        self.inner = inner
        self.down = False

    def get(self) -> PointerState | None:
        if self.down:
            raise ExternalServiceError("minio down")
        return self.inner.get()

    def set(self, champion: str, previous: str | None) -> PointerState:
        return self.inner.set(champion, previous)


def _release(store: ObjectStore, files: dict[str, bytes], version: str) -> None:
    write_bundle(store, bundle_manifest(files, version=version), files)


def test_no_pointer_means_no_model(s3_store: ObjectStore) -> None:
    provider = ModelProvider(s3_store, ObjectStorePointer(s3_store), clock=FakeClock())
    with pytest.raises(ModelNotAvailableError, match="no champion"):
        provider.get()
    assert provider.version_or_none() is None


def test_loads_champion_and_rereads_pointer_after_ttl(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    pointer = ObjectStorePointer(s3_store)
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, ttl_s=300, clock=clock)
    first = provider.get()
    assert first.manifest.version == "1"
    _release(s3_store, bundle_files, "2")
    pointer.set("2", "1")
    clock.t = 299
    assert provider.get() is first
    clock.t = 300
    assert provider.get().manifest.version == "2"
    state = provider.pointer_state()
    assert state is not None
    assert state.previous_version == "1"


def test_same_version_is_not_reloaded(
    s3_store: ObjectStore, bundle_files: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def counting(store: ObjectStore, version: str, root: str = "") -> artifacts.ModelBundle:
        calls.append(version)
        return artifacts.load_bundle(store, version, root)

    monkeypatch.setattr(model_loader, "load_bundle", counting)
    pointer = ObjectStorePointer(s3_store)
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    for t in (0, 300, 600):
        clock.t = t
        provider.get()
    assert calls == ["1"]


def test_tampered_new_version_fails_closed_then_recovers(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    pointer = ObjectStorePointer(s3_store)
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    provider.get()
    _release(s3_store, bundle_files, "2")
    s3_store.put_bytes(models_prefix("2") + "model.txt", b"tampered")
    pointer.set("2", "1")
    clock.t = 300
    with pytest.raises(ModelNotAvailableError, match="v2"):
        provider.get()
    _release(s3_store, bundle_files, "2")  # fixed by a re-release
    clock.t = 600
    assert provider.get().manifest.version == "2"


def test_storage_outage_keeps_last_good_bundle(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    pointer = FlakyPointer(ObjectStorePointer(s3_store))
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    good = provider.get()
    pointer.down = True
    clock.t = 300
    assert provider.get() is good


def test_storage_outage_before_first_load_is_unavailable(s3_store: ObjectStore) -> None:
    pointer = FlakyPointer(ObjectStorePointer(s3_store))
    pointer.down = True
    provider = ModelProvider(s3_store, pointer, clock=FakeClock())
    with pytest.raises(ModelNotAvailableError, match="unreadable"):
        provider.get()


def test_corrupt_pointer_fails_closed(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    _release(s3_store, bundle_files, "1")
    s3_store.put_bytes("models/_pointer.json", b"not json")
    provider = ModelProvider(s3_store, ObjectStorePointer(s3_store), clock=FakeClock())
    with pytest.raises(ModelNotAvailableError, match="corrupt"):
        provider.get()


def test_storage_error_loading_new_version_keeps_current_model(
    s3_store: ObjectStore, bundle_files: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer = ObjectStorePointer(s3_store)
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    good = provider.get()
    _release(s3_store, bundle_files, "2")
    pointer.set("2", "1")

    def flaky(store: ObjectStore, version: str, root: str = "") -> artifacts.ModelBundle:
        raise ExternalServiceError("get models/2/model.txt timed out")

    monkeypatch.setattr(model_loader, "load_bundle", flaky)
    clock.t = 300
    assert provider.get() is good
    monkeypatch.setattr(model_loader, "load_bundle", artifacts.load_bundle)
    clock.t = 300 + model_loader.RETRY_AFTER_S  # retried soon, not after a full TTL
    assert provider.get().manifest.version == "2"


def test_storage_error_before_first_load_retries_soon(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    pointer = FlakyPointer(ObjectStorePointer(s3_store))
    _release(s3_store, bundle_files, "1")
    pointer.set("1", None)
    pointer.down = True
    clock = FakeClock()
    provider = ModelProvider(s3_store, pointer, clock=clock)
    with pytest.raises(ModelNotAvailableError):
        provider.get()
    pointer.down = False
    clock.t = model_loader.RETRY_AFTER_S
    assert provider.get().manifest.version == "1"
