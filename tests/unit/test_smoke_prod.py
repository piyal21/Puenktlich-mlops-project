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


def test_main_prints_one_line_per_check_and_exits_1_on_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    site = good_site() | {("GET", "/api/docs"): smoke.Response(502, {}, b"")}
    monkeypatch.setattr(smoke, "http_fetch", fetch_from(site))
    assert smoke.main(["--base", BASE]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(smoke.CHECKS)
    assert "FAIL api_docs" in lines[smoke.CHECKS_ORDER.index("api_docs")]
