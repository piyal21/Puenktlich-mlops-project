"""DB Timetables API client (architecture §8): httpx with timeouts, tenacity retries.

Retries only HTTP 429/5xx and connection errors (max 3 attempts, exponential backoff + jitter);
every attempt passes the rate limiter. Credentials never appear in reprs, logs or errors.
"""

from dataclasses import dataclass
from datetime import datetime

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from dbdelay.errors import ExternalServiceError
from dbdelay.timetables.parser import BERLIN
from dbdelay.timetables.ratelimit import RateLimiter

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: bytes


def _retryable(response: ApiResponse) -> bool:
    return response.status == 429 or response.status >= 500  # noqa: PLR2004


def _give_up(state: RetryCallState) -> ApiResponse:
    """After the last attempt: return the last response, or fail on a connection error."""
    outcome = state.outcome
    if outcome is None:  # pragma: no cover - tenacity always sets it
        raise ExternalServiceError("DB API call made no attempt")
    if outcome.failed:
        exc = outcome.exception()
        raise ExternalServiceError(f"DB API unreachable: {type(exc).__name__}") from None
    result: ApiResponse = outcome.result()
    return result


class TimetablesClient:
    def __init__(  # noqa: PLR0913 - keyword-only seams for tests
        self,
        client_id: str,
        api_key: str,
        *,
        limiter: RateLimiter,
        base_url: str,
        timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        wait: wait_base | None = None,
    ) -> None:
        self._limiter = limiter
        self._base_url = base_url.rstrip("/")
        self._wait = wait or wait_exponential_jitter(initial=1, max=10)
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={
                "DB-Client-Id": client_id,
                "DB-Api-Key": api_key,
                "Accept": "application/xml",
            },
            timeout=timeout_s,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"TimetablesClient(base_url={self._base_url!r})"

    def _attempt(self, path: str) -> ApiResponse:
        self._limiter.acquire()
        response = self._http.get(path)
        return ApiResponse(status=response.status_code, body=response.content)

    def _get(self, path: str) -> ApiResponse:
        retrying = Retrying(
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=self._wait,
            retry=retry_if_exception_type(httpx.TransportError) | retry_if_result(_retryable),
            retry_error_callback=_give_up,
        )
        result: ApiResponse = retrying(self._attempt, path)
        return result

    def plan(self, eva: str, hour_local: datetime) -> ApiResponse:
        """Planned stops of one Berlin hour (``hour_local`` must be timezone-aware).

        Raises:
            ExternalServiceError: every attempt failed to connect.
        """
        local = hour_local.astimezone(BERLIN)
        return self._get(f"/plan/{eva}/{local:%y%m%d}/{local:%H}")

    def fchg(self, eva: str) -> ApiResponse:
        """All known changes for a station ("full changes").

        Raises:
            ExternalServiceError: every attempt failed to connect.
        """
        return self._get(f"/fchg/{eva}")

    def close(self) -> None:
        self._http.close()
