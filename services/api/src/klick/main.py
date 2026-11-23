"""Punto de entrada de la API de Klick."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from klick.core.config import Settings, get_settings
from klick.core.crypto import Crypto
from klick.core.db import Database
from klick.core.email import EmailSender, build_email_sender
from klick.core.errors import register_error_handlers
from klick.core.health import register_readiness_check
from klick.core.health import router as health_router
from klick.core.logging import configure_logging
from klick.core.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from klick.core.ratelimit import RateLimiter
from klick.identity.mfa import MfaService
from klick.identity.oidc import OidcProvider, OidcService, build_oidc_provider
from klick.identity.router import auth_router, me_router
from klick.identity.service import IdentityService
from klick.orgs.router import (
    invitations_router,
    members_router,
    orgs_router,
    tokens_router,
)
from klick.orgs.service import OrgService

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    email_sender: EmailSender | None = None,
    oidc_provider: OidcProvider | None | Literal["auto"] = "auto",
) -> FastAPI:
    settings = settings or get_settings()
    provider = build_oidc_provider(settings) if oidc_provider == "auto" else oidc_provider
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings)
        redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
        app.state.db = db
        app.state.redis = redis
        limiter = RateLimiter(redis)
        mfa = MfaService(
            db, redis, limiter, Crypto(settings.encryption_master_key.get_secret_value()), settings
        )
        app.state.mfa_service = mfa
        app.state.oidc_service = OidcService(db, redis, provider, settings)
        app.state.identity_service = IdentityService(
            db, limiter, email_sender or build_email_sender(settings), settings, mfa
        )
        app.state.org_service = OrgService(
            db, email_sender or build_email_sender(settings), settings
        )
        register_readiness_check(app, "db", db.ping)
        register_readiness_check(app, "redis", redis.ping)  # type: ignore[arg-type]
        yield
        await redis.aclose()
        await db.dispose()

    app = FastAPI(
        lifespan=lifespan,
        title="Klick API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None if settings.is_production else f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.readiness_checks = {}

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
    api_router = APIRouter(prefix=API_PREFIX)
    api_router.include_router(auth_router)
    api_router.include_router(me_router)
    api_router.include_router(tokens_router)
    api_router.include_router(orgs_router)
    api_router.include_router(members_router)
    api_router.include_router(invitations_router)
    app.include_router(api_router)
    return app


app = create_app()
