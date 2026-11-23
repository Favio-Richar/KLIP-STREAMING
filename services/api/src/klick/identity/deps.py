"""Dependencias de FastAPI para identidad: servicio, cookies y usuario actual."""

from typing import Annotated

from fastapi import Depends, Request, Response

from klick.core.config import Settings
from klick.identity.service import AuthContext, IdentityService, IssuedSession, unauthenticated

ACCESS_COOKIE = "klick_session"
REFRESH_COOKIE = "klick_refresh"
CSRF_COOKIE = "klick_csrf"
CSRF_HEADER = "X-CSRF-Token"
REFRESH_PATH = "/api/v1/auth"

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_identity_service(request: Request) -> IdentityService:
    service: IdentityService = request.app.state.identity_service
    return service


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def set_session_cookies(response: Response, settings: Settings, issued: IssuedSession) -> None:
    secure = settings.secure_cookies
    response.set_cookie(
        ACCESS_COOKIE,
        issued.access_token,
        max_age=settings.access_token_ttl_s,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=REFRESH_PATH,
    )
    # Legible por JavaScript a propósito (doble envío); el valor está ligado a la sesión.
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    secure = settings.secure_cookies
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_PATH, secure=secure, httponly=True, samesite="lax"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="lax")


async def current_auth(
    request: Request, service: Annotated[IdentityService, Depends(get_identity_service)]
) -> AuthContext:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise unauthenticated()
    return await service.authenticate(
        access_token=token,
        csrf_header=request.headers.get(CSRF_HEADER),
        unsafe_method=request.method not in SAFE_METHODS,
    )


CurrentAuth = Annotated[AuthContext, Depends(current_auth)]
