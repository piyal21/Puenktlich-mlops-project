"""Champion pointer: which model version serving uses (MinIO object now, SSM in Phase 6)."""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.errors import ArtifactIntegrityError, NotFoundError
from dbdelay.storage import ObjectStore

POINTER_KEY = "models/_pointer.json"


class PointerState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    champion_version: str
    previous_version: str | None
    updated_at: datetime


class ModelPointer(Protocol):
    def get(self) -> PointerState | None: ...
    def set(self, champion: str, previous: str | None) -> PointerState: ...


class ObjectStorePointer:
    """`ModelPointer` stored as one JSON object in the models bucket."""

    def __init__(self, store: ObjectStore, root: str = "") -> None:
        self._store = store
        self._key = root + POINTER_KEY

    def get(self) -> PointerState | None:
        """Current pointer, or ``None`` before the first release.

        Raises:
            ArtifactIntegrityError: if the pointer object is not a valid ``PointerState``.
        """
        try:
            raw = self._store.get_bytes(self._key)
        except NotFoundError:
            return None
        try:
            return PointerState.model_validate_json(raw)
        except ValidationError:
            raise ArtifactIntegrityError("model pointer is corrupt") from None

    def set(self, champion: str, previous: str | None) -> PointerState:
        state = PointerState(
            champion_version=champion, previous_version=previous, updated_at=datetime.now(UTC)
        )
        self._store.put_bytes(
            self._key, state.model_dump_json(indent=2).encode(), "application/json"
        )
        return state
