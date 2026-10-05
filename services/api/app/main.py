"""FastAPI app factory (architecture §7). `app` is the uvicorn/Mangum entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.deps import ServingDeps
from app.errors import REQUEST_ID_HEADER, install_error_handlers, request_context
from app.routers import health, stations
from dbdelay.config import get_settings

API_PREFIX = "/api/v1"


def create_app(deps: ServingDeps | None = None) -> FastAPI:
    """Build the app; ``deps=None`` builds them from the environment on first request."""
    app = FastAPI(
        title="Pünktlich API",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.deps = deps
    origins = deps.settings.cors_origins if deps is not None else get_settings().cors_origins
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
        )
    app.middleware("http")(request_context)
    install_error_handlers(app)
    for module in (health, stations):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()
