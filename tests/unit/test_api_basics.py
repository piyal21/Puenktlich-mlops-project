import re

import pytest
from httpx import Response

from dbdelay.storage import ObjectStore
from tests.api_support import client, make_deps
from tests.boards import NOW, departure, live_board

PROBLEM = "application/problem+json"


def assert_problem(response: Response, status: int, type_: str) -> dict[str, object]:
    assert response.status_code == status
    assert response.headers["content-type"].startswith(PROBLEM)
    body: dict[str, object] = response.json()
    assert body["type"] == type_
    assert body["status"] == status
    assert body["request_id"] == response.headers["x-request-id"]
    assert "Traceback" not in response.text
    return body


def test_health_without_board_or_model(s3_store: ObjectStore) -> None:
    response = client(make_deps(s3_store)).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model_version": None, "board_generated_at": None}


def test_health_reports_model_and_board(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    deps = make_deps(s3_store, bundle_files=bundle_files, board=live_board(departure(5)))
    body = client(deps).get("/api/v1/health").json()
    assert body["model_version"] == "1"
    assert body["board_generated_at"].startswith("2026-10-05T13:00:00")


def test_stations_search(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    assert len(api.get("/api/v1/stations").json()) == 30
    assert api.get("/api/v1/stations", params={"q": "muenchen"}).json() == [
        {"eva": "8000261", "name": "München Hbf", "state": "BY"}
    ]


def test_long_query_is_a_validation_problem(s3_store: ObjectStore) -> None:
    response = client(make_deps(s3_store)).get("/api/v1/stations", params={"q": "x" * 51})
    body = assert_problem(response, 422, "/errors/validation")
    assert "q" in str(body["detail"])


def test_request_id_is_echoed_or_replaced(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    echoed = api.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
    replaced = api.get("/api/v1/health", headers={"X-Request-ID": "bad id; Injected: 1"})
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers["x-request-id"])


def test_unknown_route_is_a_problem(s3_store: ObjectStore) -> None:
    assert_problem(client(make_deps(s3_store)).get("/api/v1/nope"), 404, "/errors/http")


def test_post_body_limits(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    too_big = api.post("/api/v1/predict", content=b"x" * 4097)
    assert_problem(too_big, 413, "/errors/payload-too-large")
    chunked = api.post("/api/v1/predict", content=iter([b"{}"]))
    assert_problem(chunked, 411, "/errors/length-required")


def test_unexpected_error_is_a_500_problem_without_trace(
    s3_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps = make_deps(s3_store)

    def boom() -> str | None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(deps.models, "version_or_none", boom)
    response = client(deps, raise_server_exceptions=False).get("/api/v1/health")
    body = assert_problem(response, 500, "/errors/internal")
    assert "secret internals" not in response.text
    assert body["instance"] == "/api/v1/health"


def test_openapi_docs(s3_store: ObjectStore) -> None:
    api = client(make_deps(s3_store))
    assert api.get("/api/docs").status_code == 200
    assert "/api/v1/stations" in api.get("/api/openapi.json").json()["paths"]


def test_cors_only_when_configured(s3_store: ObjectStore) -> None:
    preflight = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    plain = client(make_deps(s3_store)).options("/api/v1/health", headers=preflight)
    assert "access-control-allow-origin" not in plain.headers
    dev = client(make_deps(s3_store, cors_origins=["http://localhost:5173"]))
    allowed = dev.options("/api/v1/health", headers=preflight)
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_clock_is_injected(s3_store: ObjectStore) -> None:
    assert make_deps(s3_store).clock() == NOW
