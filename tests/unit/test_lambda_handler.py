import base64 as base64_module
import json
from types import SimpleNamespace
from typing import Any

import lambda_handler
from app.main import app
from dbdelay.storage import ObjectStore
from tests.api_support import make_deps
from tests.unit.test_api_routes import BODY

EVENT: dict[str, Any] = {
    "version": "2.0",
    "routeKey": "$default",
    "rawPath": "/api/v1/health",
    "rawQueryString": "",
    "headers": {"host": "api.example", "x-request-id": "lambda-1"},
    "requestContext": {
        "accountId": "000000000000",
        "apiId": "api",
        "domainName": "api.example",
        "domainPrefix": "api",
        "http": {
            "method": "GET",
            "path": "/api/v1/health",
            "protocol": "HTTP/1.1",
            "sourceIp": "203.0.113.1",
            "userAgent": "pytest",
        },
        "requestId": "req",
        "routeKey": "$default",
        "stage": "$default",
        "time": "05/Oct/2026:13:00:00 +0000",
        "timeEpoch": 1791205200000,
    },
    "isBase64Encoded": False,
}


def test_lambda_handler_serves_the_app(s3_store: ObjectStore) -> None:
    app.state.deps = make_deps(s3_store)  # the app object Mangum wraps
    try:
        result = lambda_handler.handler(EVENT, SimpleNamespace(aws_request_id="ctx"))
    finally:
        app.state.deps = None
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["status"] == "ok"
    assert result["headers"]["x-request-id"] == "lambda-1"


def _post_event(body: str, headers: dict[str, str], *, base64: bool = False) -> dict[str, Any]:
    http = dict(EVENT["requestContext"]["http"], method="POST", path="/api/v1/predict")
    return EVENT | {
        "rawPath": "/api/v1/predict",
        "headers": {"host": "api.example", "content-type": "application/json"} | headers,
        "requestContext": EVENT["requestContext"] | {"http": http},
        "body": base64_module.b64encode(body.encode()).decode() if base64 else body,
        "isBase64Encoded": base64,
    }


def _invoke(store: ObjectStore, event: dict[str, Any], **deps: Any) -> dict[str, Any]:
    app.state.deps = make_deps(store, **deps)
    try:
        return lambda_handler.handler(event, SimpleNamespace(aws_request_id="c"))
    finally:
        app.state.deps = None


def _problem_type(result: dict[str, Any]) -> str:
    body = result["body"]
    if result.get("isBase64Encoded"):
        body = base64_module.b64decode(body).decode()
    return str(json.loads(body)["type"])


def test_lambda_post_without_content_length_reaches_route(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    result = _invoke(s3_store, _post_event(json.dumps(BODY), {}), bundle_files=bundle_files)
    assert result["statusCode"] == 200


def test_lambda_post_with_content_length_unchanged(
    s3_store: ObjectStore, bundle_files: dict[str, bytes]
) -> None:
    body = json.dumps(BODY)
    event = _post_event(body, {"content-length": str(len(body))})
    assert _invoke(s3_store, event, bundle_files=bundle_files)["statusCode"] == 200


def test_lambda_post_oversize_without_header_is_413(s3_store: ObjectStore) -> None:
    result = _invoke(s3_store, _post_event("x" * 5000, {}))
    assert result["statusCode"] == 413
    assert _problem_type(result) == "/errors/payload-too-large"


def test_lambda_post_base64_oversize_is_413(s3_store: ObjectStore) -> None:
    result = _invoke(s3_store, _post_event("x" * 5000, {}, base64=True))
    assert result["statusCode"] == 413
