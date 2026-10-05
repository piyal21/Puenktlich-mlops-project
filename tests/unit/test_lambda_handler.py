import json
from types import SimpleNamespace

import lambda_handler
from app.main import app
from dbdelay.storage import ObjectStore
from tests.api_support import make_deps

EVENT = {
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
