"""Problem details (RFC 9457), request ids and body-size limits (rules.md §6.3, §7)."""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from dbdelay.errors import ExternalServiceError, ModelNotAvailableError, NotFoundError
from dbdelay.logging import get_logger

PROBLEM_JSON = "application/problem+json"
REQUEST_ID_HEADER = "X-Request-ID"
MAX_BODY_BYTES = 4096
_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


@dataclass(frozen=True)
class ProblemKind:
    type: str
    title: str
    status: int


STATION_NOT_SUPPORTED = ProblemKind("/errors/station-not-supported", "Station not supported", 404)
VALIDATION = ProblemKind("/errors/validation", "Invalid request", 422)
LENGTH_REQUIRED = ProblemKind("/errors/length-required", "Content-Length required", 411)
PAYLOAD_TOO_LARGE = ProblemKind("/errors/payload-too-large", "Request body too large", 413)
MODEL_UNAVAILABLE = ProblemKind(
    "/errors/model-unavailable", "Forecasts are temporarily unavailable", 503
)
BOARD_UNAVAILABLE = ProblemKind(
    "/errors/board-unavailable", "Live data is temporarily unavailable", 503
)
INTERNAL = ProblemKind("/errors/internal", "Internal error", 500)


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) else uuid.uuid4().hex


def problem(request: Request, kind: ProblemKind, detail: str) -> JSONResponse:
    rid = request_id_of(request)
    body = {
        "type": kind.type,
        "title": kind.title,
        "status": kind.status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": rid,
    }
    return JSONResponse(
        body, status_code=kind.status, media_type=PROBLEM_JSON, headers={REQUEST_ID_HEADER: rid}
    )


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Assign a request id (a safe incoming one is reused) and enforce the body limit."""
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request.state.request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
    if request.method in _BODY_METHODS:
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return problem(request, LENGTH_REQUIRED, "Send the body with a Content-Length header.")
        if int(length) > MAX_BODY_BYTES:
            return problem(
                request, PAYLOAD_TOO_LARGE, f"The body must be at most {MAX_BODY_BYTES} bytes."
            )
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request.state.request_id
    return response


def _validation_detail(exc: RequestValidationError) -> str:
    # Field paths and messages only, never the submitted values.
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
    )


def install_error_handlers(app: FastAPI) -> None:
    """Map project exceptions to problem responses (rules.md §6.3)."""

    async def not_found(request: Request, exc: Exception) -> Response:
        return problem(request, STATION_NOT_SUPPORTED, str(exc))

    async def invalid(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, RequestValidationError):  # registered for this type only
            raise exc
        return problem(request, VALIDATION, _validation_detail(exc))

    async def no_model(request: Request, exc: Exception) -> Response:
        get_logger("api").warning(
            "model unavailable", extra={"request_id": request_id_of(request), "error": str(exc)}
        )
        return problem(request, MODEL_UNAVAILABLE, "No forecast model is available right now.")

    async def no_board(request: Request, exc: Exception) -> Response:
        get_logger("api").warning(
            "live board unavailable",
            extra={"request_id": request_id_of(request), "error": str(exc)},
        )
        return problem(
            request,
            BOARD_UNAVAILABLE,
            "Live departures could not be loaded. Try again in a minute.",
        )

    async def http_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, StarletteHTTPException):  # registered for this type only
            raise exc
        kind = ProblemKind("/errors/http", HTTPStatus(exc.status_code).phrase, exc.status_code)
        return problem(request, kind, str(exc.detail))

    async def unexpected(request: Request, exc: Exception) -> Response:
        get_logger("api").exception("unhandled error", extra={"request_id": request_id_of(request)})
        return problem(request, INTERNAL, "Something went wrong on our side.")

    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(RequestValidationError, invalid)
    app.add_exception_handler(ModelNotAvailableError, no_model)
    app.add_exception_handler(ExternalServiceError, no_board)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(Exception, unexpected)
