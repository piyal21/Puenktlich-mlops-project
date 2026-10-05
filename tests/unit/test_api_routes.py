from datetime import UTC, datetime, timedelta

import pytest

from dbdelay.registry.artifacts import models_prefix
from dbdelay.serving.board import LiveBoard
from dbdelay.storage import ObjectStore
from tests.api_support import client, make_deps
from tests.boards import FRANKFURT, MUENCHEN, NOW, departure, live_board
from tests.unit.test_api_basics import assert_problem

URL = f"/api/v1/stations/{FRANKFURT}/departures"
BODY = {
    "eva": FRANKFURT,
    "train_type": "ICE",
    "train_number": "1602",
    "line_number": None,
    "final_destination": "Berlin Hbf",
    "stop_index": 7,
    "planned_departure": "2026-10-05T17:10:00+02:00",
}


def _board() -> LiveBoard:
    return live_board(
        departure(5),
        departure(20, delay_min=8, changed_departure_utc=NOW + timedelta(minutes=28), stop_index=8),
        departure(40, is_cancelled=True, changed_departure_utc=None, delay_min=None),
        departure(15, eva=MUENCHEN),
    )


def test_board_with_predictions(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=_board()))
    body = api.get(URL).json()
    assert body["station"] == {"eva": FRANKFURT, "name": "Frankfurt (Main) Hbf"}
    assert (body["model_version"], body["data_source"], body["stale"]) == ("1", "sample", False)
    assert body["replayed_from"] == "2026-08-24"
    assert body["data_as_of"] == "2026-10-05T15:00:00+02:00"
    first, delayed, cancelled = body["departures"]
    assert first["planned_departure"] == "2026-10-05T15:05:00+02:00"
    assert first["train"] == {
        "type": "ICE",
        "number": "1602",
        "line": None,
        "destination": "Berlin Hbf",
    }
    assert first["platform"] == "7"
    assert 0 <= first["prediction"]["p_late"] <= 1
    assert first["prediction"]["risk_level"] in {"low", "medium", "high"}
    assert 1 <= len(first["prediction"]["top_factors"]) <= 3
    assert set(first["prediction"]["top_factors"][0]) == {"feature", "direction", "text"}
    assert delayed["live_delay_min"] == 8
    assert delayed["live_departure"] == "2026-10-05T15:28:00+02:00"
    assert cancelled["cancelled"] is True
    assert cancelled["prediction"] is None


def test_hours_filter(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    board = live_board(departure(30), departure(90))
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=board))
    assert len(api.get(URL, params={"hours": 1}).json()["departures"]) == 1
    assert len(api.get(URL, params={"hours": 6}).json()["departures"]) == 2


def test_board_without_model_still_lists_departures(s3_store: ObjectStore) -> None:
    body = client(make_deps(s3_store, board=_board())).get(URL).json()
    assert body["model_version"] is None
    assert len(body["departures"]) == 3
    assert all(d["prediction"] is None for d in body["departures"])


def test_board_with_tampered_model_degrades(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    deps = make_deps(s3_store, bundle_files=bundle_files, board=_board())
    s3_store.put_bytes(models_prefix("1") + "model.txt", b"tampered")
    body = client(deps).get(URL).json()
    assert body["model_version"] is None


def test_stale_board(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    board = live_board(departure(5), generated_at=NOW - timedelta(minutes=21))
    body = client(make_deps(s3_store, bundle_files=bundle_files, board=board)).get(URL).json()
    assert body["stale"] is True


def test_station_without_departures_is_empty(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=_board()))
    body = api.get("/api/v1/stations/8010101/departures").json()  # Erfurt: supported, no rows
    assert body["departures"] == []


def test_departure_times_use_berlin_offset_in_winter(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    winter = datetime(2026, 12, 7, 13, 0, tzinfo=UTC)
    board = live_board(
        departure(0, planned_departure_utc=winter + timedelta(minutes=5)), generated_at=winter
    )
    api = client(make_deps(s3_store, now=winter, bundle_files=bundle_files, board=board))
    assert api.get(URL).json()["departures"][0]["planned_departure"] == "2026-12-07T14:05:00+01:00"


@pytest.mark.parametrize(
    ("url", "status", "type_"),
    [
        ("/api/v1/stations/1234567/departures", 404, "/errors/station-not-supported"),
        ("/api/v1/stations/abc/departures", 422, "/errors/validation"),
        (URL + "?hours=0", 422, "/errors/validation"),
        (URL + "?hours=7", 422, "/errors/validation"),
    ],
)
def test_board_input_problems(s3_store: ObjectStore, url: str, status: int, type_: str) -> None:
    assert_problem(client(make_deps(s3_store, board=_board())).get(url), status, type_)


def test_missing_board_is_503(s3_store: ObjectStore) -> None:
    assert_problem(client(make_deps(s3_store)).get(URL), 503, "/errors/board-unavailable")


def test_predict(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    api = client(make_deps(s3_store, bundle_files=bundle_files))
    body = api.post("/api/v1/predict", json=BODY).json()
    assert body["model_version"] == "1"
    assert 0 <= body["p_late"] <= 1
    assert body["risk_level"] in {"low", "medium", "high"}
    assert 1 <= len(body["top_factors"]) <= 3


def test_predict_station_unseen_by_model(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    # The test model only saw Frankfurt and München; Erfurt and train type "FLX" map to OTHER.
    body = BODY | {"eva": "8010101", "train_type": "flx"}
    api = client(make_deps(s3_store, bundle_files=bundle_files))
    response = api.post("/api/v1/predict", json=body)
    assert response.status_code == 200
    assert 0 <= response.json()["p_late"] <= 1


def test_predict_matches_board(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    row = departure(5)
    api = client(make_deps(s3_store, bundle_files=bundle_files, board=live_board(row)))
    board_p = api.get(URL).json()["departures"][0]["prediction"]["p_late"]
    single = BODY | {
        "stop_index": row.stop_index,
        "planned_departure": row.planned_departure_utc.isoformat(),
    }
    assert api.post("/api/v1/predict", json=single).json()["p_late"] == board_p


@pytest.mark.parametrize(
    ("change", "status", "type_"),
    [
        ({"planned_departure": "2026-10-05T17:10:00"}, 422, "/errors/validation"),  # naive
        ({"surprise": 1}, 422, "/errors/validation"),
        ({"stop_index": 0}, 422, "/errors/validation"),
        ({"planned_departure": "2300-01-01T10:00:00+00:00"}, 422, "/errors/validation"),
        ({"planned_departure": "0001-01-02T10:00:00+00:00"}, 422, "/errors/validation"),
        ({"planned_departure": "1900-01-01T10:00:00+00:00"}, 422, "/errors/validation"),
        ({"eva": "1234567"}, 404, "/errors/station-not-supported"),
    ],
)
def test_predict_input_problems(
    s3_store: ObjectStore,
    bundle_files: dict[str, bytes],
    change: dict[str, object],
    status: int,
    type_: str,
) -> None:
    api = client(make_deps(s3_store, bundle_files=bundle_files))
    assert_problem(api.post("/api/v1/predict", json=BODY | change), status, type_)


def test_predict_without_model_is_503(s3_store: ObjectStore) -> None:
    response = client(make_deps(s3_store)).post("/api/v1/predict", json=BODY)
    assert_problem(response, 503, "/errors/model-unavailable")


def test_model_info(s3_store: ObjectStore, bundle_files: dict[str, bytes]) -> None:
    body = client(make_deps(s3_store, bundle_files=bundle_files)).get("/api/v1/model").json()
    assert body["version"] == "1"
    assert body["previous_version"] is None
    assert body["model_name"] == "m"
    assert body["data_snapshot_id"] == "2026-03-31_abcdef12"
    assert body["metrics"]["test_brier"] == 0.14
    assert body["metrics"]["baseline_brier"] == 0.16
    assert body["risk_thresholds"] == {"medium": 0.2, "high": 0.45}
    assert body["train_window"] == {"start": "2026-03-01", "end": "2026-03-17"}


def test_model_info_without_model_is_503(s3_store: ObjectStore) -> None:
    response = client(make_deps(s3_store)).get("/api/v1/model")
    assert_problem(response, 503, "/errors/model-unavailable")
