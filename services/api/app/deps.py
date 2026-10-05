"""Collaborators of the API, built once per process (or injected by tests)."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import Depends, Request

from dbdelay.config import Settings, get_settings
from dbdelay.data.stations import Station, load_stations
from dbdelay.errors import NotFoundError
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.serving.board import BoardSource
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.storage import ObjectStore, make_s3_client

_BUILD_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ServingDeps:
    settings: Settings
    stations: tuple[Station, ...]
    models: ModelProvider
    board: BoardSource
    clock: Callable[[], datetime] = field(default=_utc_now)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ServingDeps":
        client = make_s3_client(settings)
        model_store = ObjectStore(client, settings.models_bucket)
        data_store = ObjectStore(client, settings.data_bucket)
        return cls(
            settings=settings,
            stations=tuple(load_stations(settings.stations_file)),
            models=ModelProvider(
                model_store, ObjectStorePointer(model_store), ttl_s=settings.model_pointer_ttl_s
            ),
            board=BoardSource(data_store, settings.board_key, ttl_s=settings.board_ttl_s),
        )

    def station(self, eva: str) -> Station:
        """Raises ``NotFoundError`` for an EVA outside the supported list."""
        for station in self.stations:
            if station.eva == eva:
                return station
        raise NotFoundError(f"EVA {eva} is not in the supported station list.")


def get_deps(request: Request) -> ServingDeps:
    """Injected deps, or deps from the environment on first use (Lambda/uvicorn)."""
    state = request.app.state
    if state.deps is None:
        with _BUILD_LOCK:
            if state.deps is None:
                state.deps = ServingDeps.from_settings(get_settings())
    return cast(ServingDeps, state.deps)


Deps = Annotated[ServingDeps, Depends(get_deps)]
