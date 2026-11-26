"""FastAPI app factory (architecture §7). `app` is the uvicorn/Mangum entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from app.deps import ServingDeps
from app.errors import REQUEST_ID_HEADER, install_error_handlers, request_context
from app.routers import departures, health, model, monitoring, predict, stations
from dbdelay.config import get_settings

API_PREFIX = "/api/v1"
OPENAPI_URL = "/api/openapi.json"
# Exact Swagger UI release (security review S2): FastAPI's default `swagger-ui-dist@5` follows
# every new 5.x. jsdelivr npm files are immutable per version; the docs CSP in
# infra/modules/web_cdn allows only this path. Bump both together.
SWAGGER_UI_BASE = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.34.1/"


def create_app(deps: ServingDeps | None = None) -> FastAPI:
    """Build the app; ``deps=None`` builds them from the environment on first request."""
    app = FastAPI(
        title="Pünktlich API",
        version="1.0.0",
        docs_url=None,  # served below with a pinned Swagger UI
        redoc_url=None,
        openapi_url=OPENAPI_URL,
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

    @app.get("/api/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=OPENAPI_URL,
            title=f"{app.title} - docs",
            swagger_js_url=SWAGGER_UI_BASE + "swagger-ui-bundle.js",
            swagger_css_url=SWAGGER_UI_BASE + "swagger-ui.css",
        )

    for module in (health, stations, departures, predict, model, monitoring):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()
