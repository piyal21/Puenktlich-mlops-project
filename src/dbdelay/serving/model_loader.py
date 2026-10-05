"""Champion model for serving: pointer (re-read every TTL) → verified bundle (architecture §6, §7).

Integrity problems fail closed (no forecasts); a storage outage keeps the last good bundle.
"""

import threading
import time
from collections.abc import Callable

from dbdelay.errors import ArtifactIntegrityError, ExternalServiceError, ModelNotAvailableError
from dbdelay.logging import get_logger
from dbdelay.registry.artifacts import ModelBundle, load_bundle
from dbdelay.registry.pointer import ModelPointer, PointerState
from dbdelay.storage import ObjectStore

Clock = Callable[[], float]
# After a transient storage error, try again this soon instead of after a full TTL.
RETRY_AFTER_S = 15.0


class ModelProvider:
    """Thread-safe cache of the champion bundle (one load per version)."""

    def __init__(
        self,
        store: ObjectStore,
        pointer: ModelPointer,
        *,
        ttl_s: float = 300.0,
        clock: Clock = time.monotonic,
        root: str = "",
    ) -> None:
        self._store = store
        self._pointer = pointer
        self._ttl_s = ttl_s
        self._clock = clock
        self._root = root
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._state: PointerState | None = None
        self._bundle: ModelBundle | None = None
        self._problem = "model not loaded yet"

    def get(self) -> ModelBundle:
        """The champion bundle.

        Raises:
            ModelNotAvailableError: no champion released, or it failed verification/loading.
        """
        with self._lock:
            now = self._clock()
            if self._checked_at is None or now - self._checked_at >= self._ttl_s:
                self._checked_at = now
                self._refresh(now)
            if self._bundle is None:
                raise ModelNotAvailableError(self._problem)
            return self._bundle

    def version_or_none(self) -> str | None:
        """Loaded champion version, or ``None`` (for `/health`; never raises)."""
        try:
            return self.get().manifest.version
        except ModelNotAvailableError:
            return None

    def pointer_state(self) -> PointerState | None:
        """Pointer as of the last successful read (for ``previous_version``)."""
        with self._lock:
            return self._state

    def _refresh(self, now: float) -> None:
        try:
            state = self._pointer.get()
        except ExternalServiceError as exc:
            self._transient(now, "model pointer unreadable", exc)
            return
        except ArtifactIntegrityError as exc:
            self._fail(str(exc))
            return
        if state is None:
            self._fail("no champion model released yet")
            return
        self._state = state
        if self._bundle is not None and self._bundle.manifest.version == state.champion_version:
            return
        try:
            self._bundle = load_bundle(self._store, state.champion_version, self._root)
        except ExternalServiceError as exc:
            self._transient(now, f"champion v{state.champion_version} not reachable", exc)
            return
        except ArtifactIntegrityError as exc:
            self._fail(f"champion v{state.champion_version} not loadable: {exc}")
            return
        get_logger("api").info("champion loaded", extra={"model_version": state.champion_version})

    def _transient(self, now: float, problem: str, exc: ExternalServiceError) -> None:
        """Storage hiccup: keep serving the current bundle (if any) and retry soon."""
        get_logger("api").warning(problem, extra={"error": str(exc)})
        self._checked_at = now - self._ttl_s + RETRY_AFTER_S
        if self._bundle is None:
            self._problem = problem

    def _fail(self, problem: str) -> None:
        self._bundle = None
        self._problem = problem
        get_logger("api").error("champion unavailable", extra={"problem": problem})
