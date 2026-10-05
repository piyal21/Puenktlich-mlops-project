"""Build the FastAPI app against moto storage with a fixed clock (no network)."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.deps import ServingDeps
from app.main import create_app
from dbdelay.config import Settings
from dbdelay.data.stations import load_stations
from dbdelay.registry.artifacts import write_bundle
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.serving.board import BOARD_KEY, BoardSource, LiveBoard, board_to_bytes
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.storage import ObjectStore
from tests.boards import NOW
from tests.bundles import bundle_manifest

STATIONS = tuple(load_stations(Path(__file__).resolve().parents[1] / "configs" / "stations.yaml"))


def make_deps(
    store: ObjectStore,
    *,
    now: datetime = NOW,
    bundle_files: dict[str, bytes] | None = None,
    board: LiveBoard | None = None,
    cors_origins: Sequence[str] = (),
) -> ServingDeps:
    """Deps on one moto bucket; ``bundle_files`` releases them as champion v1."""
    if bundle_files is not None:
        write_bundle(store, bundle_manifest(bundle_files, version="1"), bundle_files)
        ObjectStorePointer(store).set("1", None)
    if board is not None:
        store.put_bytes(BOARD_KEY, board_to_bytes(board))
    settings = Settings(_env_file=None, cors_origins=list(cors_origins))  # type: ignore[call-arg]
    return ServingDeps(
        settings=settings,
        stations=STATIONS,
        models=ModelProvider(store, ObjectStorePointer(store)),
        board=BoardSource(store),
        clock=lambda: now,
    )


def client(deps: ServingDeps, **kwargs: Any) -> TestClient:
    return TestClient(create_app(deps), **kwargs)
