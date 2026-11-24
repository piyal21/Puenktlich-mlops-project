import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_prod.py"
spec = importlib.util.spec_from_file_location("smoke_prod", SCRIPT)
assert spec is not None
assert spec.loader is not None
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

BASE = "https://d1.cloudfront.net"
SPA = b'<!doctype html><html><body><div id="root"></div></body></html>'
SECURITY = {
    "strict-transport-security": "max-age=31536000",
    "content-security-policy": "x",
    "cache-control": "no-cache",
}


def _json(status: int, data: Any, ctype: str = "application/json") -> Any:
    return smoke.Response(status, {"content-type": ctype}, json.dumps(data).encode())


def good_site() -> dict[tuple[str, str], Any]:
    board = {"data_source": "sample", "stale": False, "departures": [{"event_id": "e1"}]}
    return {
        ("GET", "/api/v1/health"): _json(200, {"status": "ok", "model_version": "1"}),
        ("GET", "/api/v1/stations?q=erfurt"): _json(200, [{"eva": "8010101", "name": "Erfurt"}]),
        ("GET", "/api/v1/stations/8010101/departures?hours=3"): _json(200, board),
        ("POST", "/api/v1/predict"): _json(200, {"p_late": 0.3, "risk_level": "medium"}),
        ("GET", "/api/v1/model"): _json(200, {"version": "1"}),
        ("GET", "/api/v1/monitoring"): _json(200, {"latest": None, "history": [], "retrains": []}),
        ("GET", "/api/docs"): smoke.Response(200, {"content-type": "text/html"}, b"<html>"),
        ("GET", "/api/v1/stations/1234567/departures?hours=3"): _json(
            404, {"type": "/errors/station-not-supported"}, "application/problem+json"
        ),
        ("GET", "/station/8010101"): smoke.Response(
            200, {"content-type": "text/html"} | SECURITY, SPA
        ),
        ("GET", "/"): smoke.Response(200, {"content-type": "text/html"} | SECURITY, SPA),
    }


def fetch_from(site: dict[tuple[str, str], Any]) -> Any:
    def fetch(method: str, url: str, body: bytes | None) -> Any:
        return site[(method, url.removeprefix(BASE))]

    return fetch


def test_all_checks_pass_on_a_good_site() -> None:
    results = smoke.run(BASE, fetch_from(good_site()))
    assert [name for name, _ in results] == list(smoke.CHECKS)
    assert all(reason is None for _, reason in results), results


@pytest.mark.parametrize(
    ("key", "bad", "check"),
    [
        (
            ("GET", "/station/8010101"),
            smoke.Response(403, {}, b"AccessDenied"),
            "deep_link_serves_spa",
        ),
        (
            ("GET", "/api/v1/stations/1234567/departures?hours=3"),
            smoke.Response(404, {"content-type": "text/html"}, SPA),
            "api_404_is_problem_json",
        ),
        (
            ("GET", "/api/v1/health"),
            _json(200, {"status": "ok", "model_version": None}),
            "health_reports_model",
        ),
        (
            ("GET", "/api/v1/stations/8010101/departures?hours=3"),
            _json(200, {"data_source": "sample", "stale": True, "departures": [{}]}),
            "board_is_sample",
        ),
        (
            ("POST", "/api/v1/predict"),
            _json(411, {"type": "/errors/length-required"}),
            "predict_post",
        ),
        (
            ("GET", "/"),
            smoke.Response(200, {"content-type": "text/html", "cache-control": "no-cache"}, SPA),
            "security_headers",
        ),
        (
            ("GET", "/api/v1/monitoring"),
            _json(503, {"type": "/errors/monitoring-unavailable"}, "application/problem+json"),
            "monitoring_served",
        ),
        (
            ("GET", "/api/v1/monitoring"),
            smoke.Response(200, {"content-type": "text/html"}, SPA),
            "monitoring_served",
        ),
        (
            ("GET", "/"),
            smoke.Response(
                200, {"content-type": "text/html"} | SECURITY | {"cache-control": "max-age=60"}, SPA
            ),
            "index_html_revalidates",
        ),
    ],
)
def test_a_broken_response_fails_its_check(key: tuple[str, str], bad: Any, check: str) -> None:
    site = good_site() | {key: bad}
    failed = {name for name, reason in smoke.run(BASE, fetch_from(site)) if reason is not None}
    assert failed == {check}


@pytest.mark.parametrize("version", ["1", "7", "12"])
def test_health_accepts_any_released_champion(version: str) -> None:
    # v7 became champion on 2026-11-19; the check had "1" hard-coded and failed on the live site.
    site = good_site() | {
        ("GET", "/api/v1/health"): _json(200, {"status": "ok", "model_version": version})
    }
    assert smoke.health_reports_model(fetch_from(site), BASE) is None


@pytest.mark.parametrize("version", ["", "v7", 7])
def test_health_rejects_a_malformed_version(version: Any) -> None:
    site = good_site() | {
        ("GET", "/api/v1/health"): _json(200, {"status": "ok", "model_version": version})
    }
    assert smoke.health_reports_model(fetch_from(site), BASE) is not None


def test_main_prints_one_line_per_check_and_exits_1_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    site = good_site() | {("GET", "/api/docs"): smoke.Response(502, {}, b"")}
    monkeypatch.setattr(smoke, "http_fetch", fetch_from(site))
    assert smoke.main(["--base", BASE]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(smoke.CHECKS)
    assert "FAIL api_docs" in lines[smoke.CHECKS_ORDER.index("api_docs")]


NOW = smoke.datetime(2026, 11, 13, 9, 0, tzinfo=smoke.UTC)
BOARD_URL = ("GET", "/api/v1/stations/8010101/departures?hours=3")


def live_board(**overrides: Any) -> Any:
    board = {
        "data_source": "live",
        "stale": False,
        "data_as_of": "2026-11-13T09:52:00+01:00",  # 08:52 UTC, 8 min old
        "departures": [{"event_id": "e1", "train": {"via": ["Gotha", "Eisenach"]}}],
    } | overrides
    return _json(200, board)


def live_failures(board: Any) -> set[str]:
    site = good_site() | {BOARD_URL: board}
    results = smoke.run(BASE, fetch_from(site), expect_live=True, now=NOW)
    return {name for name, reason in results if reason is not None}


def test_expect_live_passes_on_a_fresh_live_board() -> None:
    results = smoke.run(
        BASE, fetch_from(good_site() | {BOARD_URL: live_board()}), expect_live=True, now=NOW
    )
    assert "board_is_live" in [name for name, _ in results]
    assert "board_is_sample" not in [name for name, _ in results]
    assert all(reason is None for _, reason in results), results


def test_expect_live_fails_on_sample_board() -> None:
    assert live_failures(live_board(data_source="sample")) == {"board_is_live"}


def test_expect_live_fails_on_old_data_as_of() -> None:
    assert live_failures(live_board(data_as_of="2026-11-13T09:30:00+01:00")) == {"board_is_live"}


def test_expect_live_fails_without_via_list() -> None:
    board = live_board(departures=[{"event_id": "e1", "train": {"destination": "X"}}])
    assert live_failures(board) == {"board_is_live"}


def test_main_expect_live_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(smoke, "http_fetch", fetch_from(good_site()))  # sample board
    assert smoke.main(["--base", BASE, "--expect-live"]) == 1
    assert "FAIL board_is_live" in capsys.readouterr().out
