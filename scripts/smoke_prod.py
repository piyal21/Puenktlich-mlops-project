"""`make smoke-prod`: check the public site end to end through CloudFront (stdlib only).

Prints one ``PASS name`` / ``FAIL name: reason`` line per check; exit 1 if any fails.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any, NamedTuple

ERFURT = "8010101"
TIMEOUT_S = 30


class Response(NamedTuple):
    status: int
    headers: dict[str, str]  # lower-case names
    body: bytes


Fetch = Callable[[str, str, bytes | None], Response]
Check = Callable[[Fetch, str], str | None]  # None = pass, else the reason


def http_fetch(method: str, url: str, body: bytes | None) -> Response:
    request = urllib.request.Request(  # noqa: S310 - https URL given by the operator
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:  # noqa: S310
            return Response(
                resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
            )
    except urllib.error.HTTPError as err:
        return Response(err.code, {k.lower(): v for k, v in err.headers.items()}, err.read())


def _get_json(fetch: Fetch, base: str, path: str) -> tuple[Response, Any]:
    resp = fetch("GET", base + path, None)
    try:
        return resp, json.loads(resp.body)
    except ValueError:
        return resp, None


def health_reports_model(fetch: Fetch, base: str) -> str | None:
    resp, data = _get_json(fetch, base, "/api/v1/health")
    version = data.get("model_version") if isinstance(data, dict) else None
    if resp.status != HTTPStatus.OK or version != "1":
        return f"status {resp.status}, model_version {version!r}"
    return None


def stations_search(fetch: Fetch, base: str) -> str | None:
    resp, data = _get_json(fetch, base, "/api/v1/stations?q=erfurt")
    if resp.status != HTTPStatus.OK or not any(s.get("eva") == ERFURT for s in data or []):
        return f"status {resp.status}, Erfurt not found"
    return None


def board_is_sample(fetch: Fetch, base: str) -> str | None:
    resp, data = _get_json(fetch, base, f"/api/v1/stations/{ERFURT}/departures?hours=3")
    if resp.status != HTTPStatus.OK or not isinstance(data, dict):
        return f"status {resp.status}"
    if data.get("data_source") != "sample" or data.get("stale") or not data.get("departures"):
        return f"source {data.get('data_source')!r}, stale {data.get('stale')!r}, empty board"
    return None


def predict_post(fetch: Fetch, base: str) -> str | None:
    planned = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat()
    body = {
        "eva": ERFURT,
        "train_type": "ICE",
        "train_number": "1602",
        "line_number": None,
        "final_destination": "Berlin Hbf",
        "stop_index": 7,
        "planned_departure": planned,
    }
    resp = fetch("POST", base + "/api/v1/predict", json.dumps(body).encode())
    try:
        data = json.loads(resp.body)
    except ValueError:
        data = None
    if resp.status != HTTPStatus.OK or not isinstance(data, dict) or "p_late" not in data:
        return f"status {resp.status}"
    return None


def model_info(fetch: Fetch, base: str) -> str | None:
    resp = fetch("GET", base + "/api/v1/model", None)
    return None if resp.status == HTTPStatus.OK else f"status {resp.status}"


def api_docs(fetch: Fetch, base: str) -> str | None:
    resp = fetch("GET", base + "/api/docs", None)
    html = resp.headers.get("content-type", "").startswith("text/html")
    ok = resp.status == HTTPStatus.OK and html
    return None if ok else f"status {resp.status}"


def api_404_is_problem_json(fetch: Fetch, base: str) -> str | None:
    resp = fetch("GET", base + "/api/v1/stations/123/departures?hours=3", None)
    ctype = resp.headers.get("content-type", "")
    if resp.status != HTTPStatus.NOT_FOUND or not ctype.startswith("application/problem+json"):
        return f"status {resp.status}, content-type {ctype!r}"
    return None


def deep_link_serves_spa(fetch: Fetch, base: str) -> str | None:
    resp = fetch("GET", base + f"/station/{ERFURT}", None)
    if resp.status != HTTPStatus.OK or b'<div id="root">' not in resp.body:
        return f"status {resp.status}, no SPA shell"
    return None


def security_headers(fetch: Fetch, base: str) -> str | None:
    resp = fetch("GET", base + "/", None)
    missing = [
        h for h in ("strict-transport-security", "content-security-policy") if h not in resp.headers
    ]
    return f"missing {missing}" if missing else None


CHECKS: dict[str, Check] = {
    check.__name__: check
    for check in (
        health_reports_model,
        stations_search,
        board_is_sample,
        predict_post,
        model_info,
        api_docs,
        api_404_is_problem_json,
        deep_link_serves_spa,
        security_headers,
    )
}
CHECKS_ORDER = list(CHECKS)


def run(base: str, fetch: Fetch) -> list[tuple[str, str | None]]:
    base = base.rstrip("/")
    results: list[tuple[str, str | None]] = []
    for name, check in CHECKS.items():
        try:
            results.append((name, check(fetch, base)))
        except (OSError, KeyError, TypeError, AttributeError) as exc:
            results.append((name, f"{type(exc).__name__}: {exc}"))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the public site.")
    parser.add_argument("--base", required=True, help="https://<cloudfront domain>")
    args = parser.parse_args(argv)
    results = run(args.base, http_fetch)
    for name, reason in results:
        sys.stdout.write(f"PASS {name}\n" if reason is None else f"FAIL {name}: {reason}\n")
    return 1 if any(reason is not None for _, reason in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
