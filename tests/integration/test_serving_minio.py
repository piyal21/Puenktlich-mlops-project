from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import ServingDeps
from app.main import create_app
from dbdelay.config import get_settings
from dbdelay.data.stations import load_stations
from dbdelay.registry.pointer import ObjectStorePointer
from dbdelay.serving.board import BOARD_KEY, BoardSource
from dbdelay.serving.model_loader import ModelProvider
from dbdelay.serving.seed import seed_board
from dbdelay.storage import ObjectStore, make_s3_client

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


def test_seed_then_serve_real_champion(scratch: tuple[ObjectStore, str]) -> None:
    store, root = scratch
    settings = get_settings()
    models = ObjectStore(make_s3_client(settings), settings.models_bucket)
    if ObjectStorePointer(models).get() is None:
        pytest.skip("no champion in MinIO; run `make train` first")
    now = datetime.now(UTC)
    board = seed_board(store, now=now, board_key=root + BOARD_KEY)
    upcoming = [
        d.eva
        for d in board.departures
        if not d.is_cancelled and now <= d.planned_departure_utc <= now + timedelta(hours=3)
    ]
    assert upcoming, "seeded board has no departures in the next 3 h"
    busiest = Counter(upcoming).most_common(1)[0][0]
    deps = ServingDeps(
        settings=settings,
        stations=tuple(load_stations(REPO / "configs" / "stations.yaml")),
        models=ModelProvider(models, ObjectStorePointer(models)),
        board=BoardSource(store, root + BOARD_KEY),
        clock=lambda: now,
    )
    api = TestClient(create_app(deps))
    body = api.get(f"/api/v1/stations/{busiest}/departures", params={"hours": 3}).json()
    assert body["data_source"] == "sample"
    assert body["model_version"] is not None
    scored = [d["prediction"] for d in body["departures"] if d["prediction"] is not None]
    assert scored
    assert all(0 <= p["p_late"] <= 1 and 1 <= len(p["top_factors"]) <= 3 for p in scored)
    assert api.get("/api/v1/model").json()["version"] == body["model_version"]
