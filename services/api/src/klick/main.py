"""Punto de entrada de la API de Klick."""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from klick.core.config import Settings, get_settings
from klick.core.errors import register_error_handlers
from klick.core.health import router as health_router
from klick.core.logging import configure_logging
from klick.core.middleware import REQUEST_ID_HEADER, RequestIdMiddleware

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="Klick API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None if settings.is_production else f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    # El último añadido es el más externo: RequestId envuelve a CORS y a todo lo demás.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After", "ETag"],
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(APIRouter(prefix=API_PREFIX))
    return app


app = create_app()
