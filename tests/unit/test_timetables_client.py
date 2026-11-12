from datetime import datetime

import httpx
import pytest
from tenacity import wait_none

from dbdelay.errors import ExternalServiceError
from dbdelay.timetables.client import TimetablesClient
from dbdelay.timetables.parser import BERLIN
from dbdelay.timetables.ratelimit import RateLimiter

BASE = "https://db.example/timetables/v1"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class CountingLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(50)
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


def _client(handler: httpx.MockTransport, limiter: RateLimiter | None = None) -> TimetablesClient:
    return TimetablesClient(
        "client-id-value",
        "api-key-value",
        limiter=limiter or CountingLimiter(),
        base_url=BASE,
        transport=handler,
        wait=wait_none(),
    )


def test_limiter_sleeps_after_50_calls_in_a_minute() -> None:
    fake = FakeClock()
    limiter = RateLimiter(50, clock=fake.clock, sleep=fake.sleep)
    for _ in range(50):
        limiter.acquire()
        fake.now += 0.5  # 50 calls in 25 s
    assert fake.slept == []
    limiter.acquire()
    assert fake.slept == [pytest.approx(35.0)]  # the first call leaves the window at t=60


def test_limiter_allows_steady_pace_without_sleeping() -> None:
    fake = FakeClock()
    limiter = RateLimiter(50, clock=fake.clock, sleep=fake.sleep)
    for _ in range(200):
        limiter.acquire()
        fake.now += 1.25  # 48 calls per minute
    assert fake.slept == []


def test_headers_and_plan_path() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<timetable/>")

    hour = datetime(2026, 9, 25, 17, tzinfo=BERLIN)
    response = _client(httpx.MockTransport(handle)).plan("8000199", hour)
    assert response.status == 200
    assert response.body == b"<timetable/>"
    (request,) = seen
    assert request.url.path == "/timetables/v1/plan/8000199/260925/17"
    assert request.headers["DB-Client-Id"] == "client-id-value"
    assert request.headers["DB-Api-Key"] == "api-key-value"
    assert request.headers["Accept"] == "application/xml"


def test_fchg_path() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, content=b"<timetable/>")

    _client(httpx.MockTransport(handle)).fchg("8000199")
    assert seen == ["/timetables/v1/fchg/8000199"]


def test_retries_429_then_succeeds() -> None:
    statuses = iter([429, 429, 200])
    limiter = CountingLimiter()
    client = _client(httpx.MockTransport(lambda _: httpx.Response(next(statuses))), limiter)
    assert client.fchg("8000199").status == 200
    assert limiter.acquired == 3  # every attempt counts against the rate limit


def test_no_retry_on_404() -> None:
    calls: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    assert _client(httpx.MockTransport(handle)).fchg("8000199").status == 404
    assert len(calls) == 1


def test_gives_up_after_three_5xx() -> None:
    calls: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    assert _client(httpx.MockTransport(handle)).fchg("8000199").status == 503
    assert len(calls) == 3


def test_connect_error_three_times_raises() -> None:
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ExternalServiceError) as exc_info:
        _client(httpx.MockTransport(handle)).fchg("8000199")
    assert len(calls) == 3
    assert "api-key-value" not in str(exc_info.value)


def test_repr_hides_key() -> None:
    client = _client(httpx.MockTransport(lambda _: httpx.Response(200)))
    assert "api-key-value" not in repr(client)
    assert "client-id-value" not in repr(client)


def test_no_retry_on_local_protocol_error() -> None:
    # e.g. an invalid header value (a stray quote or CR in a key): our bug, not a transient one
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.LocalProtocolError("Illegal header value")

    with pytest.raises(ExternalServiceError, match="LocalProtocolError"):
        _client(httpx.MockTransport(handle)).fchg("8000199")
    assert len(calls) == 1
