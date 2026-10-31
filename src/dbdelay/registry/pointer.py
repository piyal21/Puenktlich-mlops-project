"""Champion pointer: which model version serving uses (MinIO object locally, SSM in AWS)."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ConfigDict, ValidationError

from dbdelay.config import Settings
from dbdelay.errors import ArtifactIntegrityError, ExternalServiceError, NotFoundError
from dbdelay.storage import BOTO_CONFIG, ObjectStore

POINTER_KEY = "models/_pointer.json"
# SSM cannot store an empty string; Terraform creates both parameters with this value.
NONE_VALUE = "none"


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


class SsmClient(Protocol):
    """The two SSM calls the pointer needs (a boto3 SSM client satisfies it)."""

    def get_parameters(self, *, Names: list[str]) -> Mapping[str, Any]: ...

    def put_parameter(
        self,
        *,
        Name: str,
        Value: str,
        Type: str,
        Overwrite: bool,
    ) -> Mapping[str, Any]: ...


def make_ssm_client(region: str) -> SsmClient:
    """boto3 SSM client with the project timeouts and retries (default credential chain)."""
    return cast(SsmClient, boto3.client("ssm", region_name=region, config=BOTO_CONFIG))


class SsmPointer:
    """`ModelPointer` stored as two SSM String parameters (architecture §6).

    ``previous_param`` defaults to ``champion_param`` with its last segment replaced by
    ``previous_version``. A missing parameter or the value ``none`` means "no version".
    """

    def __init__(
        self, client: SsmClient, champion_param: str, previous_param: str | None = None
    ) -> None:
        self._client = client
        self._champion = champion_param
        self._previous = previous_param or (champion_param.rsplit("/", 1)[0] + "/previous_version")

    def get(self) -> PointerState | None:
        """Current pointer, or ``None`` while no champion is set.

        Raises:
            ExternalServiceError: if SSM cannot be read.
        """
        try:
            response = self._client.get_parameters(Names=[self._champion, self._previous])
        except (BotoCoreError, ClientError) as exc:
            raise ExternalServiceError("model pointer (SSM) unreadable") from exc
        params = {p["Name"]: p for p in response.get("Parameters", [])}
        champion = params.get(self._champion)
        if champion is None or champion["Value"] == NONE_VALUE:
            return None
        previous = params.get(self._previous)
        previous_value = previous["Value"] if previous is not None else NONE_VALUE
        updated_at: datetime = champion["LastModifiedDate"]
        return PointerState(
            champion_version=champion["Value"],
            previous_version=None if previous_value == NONE_VALUE else previous_value,
            updated_at=updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC),
        )

    def set(self, champion: str, previous: str | None) -> PointerState:
        """Write ``previous`` first, then ``champion`` (the value serving reads).

        Raises:
            ExternalServiceError: if SSM cannot be written.
        """
        try:
            for name, value in (
                (self._previous, previous or NONE_VALUE),
                (self._champion, champion),
            ):
                self._client.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
        except (BotoCoreError, ClientError) as exc:
            raise ExternalServiceError("model pointer (SSM) not written") from exc
        return PointerState(
            champion_version=champion, previous_version=previous, updated_at=datetime.now(UTC)
        )


def make_pointer(settings: Settings, store: ObjectStore) -> ModelPointer:
    """SSM pointer when ``model_pointer_param`` is an SSM path (``/…``), else the MinIO object."""
    if settings.model_pointer_param.startswith("/"):
        return SsmPointer(make_ssm_client(settings.aws_region), settings.model_pointer_param)
    return ObjectStorePointer(store)
