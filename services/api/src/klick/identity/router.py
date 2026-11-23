"""Endpoints de identidad: /auth/* y /me (docs/05 §2)."""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from klick.core.errors import ApiError, problem_response
from klick.identity.deps import (
    CSRF_HEADER,
    REFRESH_COOKIE,
    REFRESH_PATH,
    CurrentAuth,
    clear_session_cookies,
    client_ip,
    get_identity_service,
    set_session_cookies,
)
from klick.identity.mfa import MfaChallenge, MfaService
from klick.identity.models import User
from klick.identity.oidc import OidcService
from klick.identity.schemas import (
    AcceptedResponse,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    MfaConfirmRequest,
    MfaConfirmResponse,
    MfaDisableRequest,
    MfaEnrollResponse,
    MfaRequiredResponse,
    MfaVerifyRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SessionOut,
    UserOut,
    VerifyEmailRequest,
)
from klick.identity.service import (
    FORGOT_ACCEPTED,
    REGISTER_ACCEPTED,
    IdentityService,
    IssuedSession,
    unauthenticated,
)

log = structlog.get_logger("klick.identity.router")

MFA_COOKIE = "klick_mfa"
OIDC_COOKIE = "klick_oidc"
MFA_COOKIE_TTL_S = 300
OIDC_COOKIE_TTL_S = 600

Service = Annotated[IdentityService, Depends(get_identity_service)]


def get_mfa_service(request: Request) -> MfaService:
    service: MfaService = request.app.state.mfa_service
    return service


def get_oidc_service(request: Request) -> OidcService:
    service: OidcService = request.app.state.oidc_service
    return service


Mfa = Annotated[MfaService, Depends(get_mfa_service)]
Oidc = Annotated[OidcService, Depends(get_oidc_service)]

auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])


def _user_out(user: User, *, mfa_enabled: bool = False) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        locale=user.locale,
        timezone=user.timezone,
        email_verified=user.email_verified_at is not None,
        mfa_enabled=mfa_enabled,
        created_at=user.created_at,
    )


def _login_json(request: Request, issued: IssuedSession, *, mfa_enabled: bool) -> JSONResponse:
    out = JSONResponse(
        LoginResponse(
            user=_user_out(issued.user, mfa_enabled=mfa_enabled), csrf_token=issued.csrf_token
        ).model_dump(mode="json")
    )
    set_session_cookies(out, request.app.state.settings, issued)
    return out


# ------------------------------------------------------------------------- registro y correo


@auth_router.post(
    "/register", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedResponse
)
async def register(body: RegisterRequest, request: Request, service: Service) -> AcceptedResponse:
    await service.register(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        ip=client_ip(request),
    )
    return AcceptedResponse(message=REGISTER_ACCEPTED)


@auth_router.post("/email/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: VerifyEmailRequest, service: Service) -> Response:
    await service.verify_email(body.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------- sesión


@auth_router.post("/login", response_model=LoginResponse | MfaRequiredResponse)
async def login(body: LoginRequest, request: Request, service: Service) -> Response:
    result = await service.login(
        email=body.email,
        password=body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    if isinstance(result, MfaChallenge):
        return JSONResponse(MfaRequiredResponse(mfa_token=result.token).model_dump(mode="json"))
    return _login_json(request, result, mfa_enabled=False)


@auth_router.post("/mfa/verify", response_model=LoginResponse)
async def mfa_verify(body: MfaVerifyRequest, request: Request, service: Service) -> Response:
    token = body.mfa_token or request.cookies.get(MFA_COOKIE)
    if not token:
        raise ApiError(
            401, "mfa_challenge_expired", "Challenge expired", "Vuelve a iniciar sesión."
        )
    issued = await service.complete_mfa_login(
        token=token,
        code=body.code,
        recovery_code=body.recovery_code,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    out = _login_json(request, issued, mfa_enabled=True)
    out.delete_cookie(MFA_COOKIE, path=REFRESH_PATH)
    return out


@auth_router.post("/refresh", response_model=LoginResponse)
async def refresh(request: Request, service: Service) -> Response:
    settings = request.app.state.settings
    try:
        token = request.cookies.get(REFRESH_COOKIE)
        if not token:
            raise unauthenticated("invalid_refresh_token")
        issued = await service.refresh(
            refresh_token=token,
            csrf_header=request.headers.get(CSRF_HEADER),
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ApiError as exc:
        if exc.status != 401:
            raise
        # Sesión muerta: se limpian las cookies para que el cliente vuelva a iniciar sesión.
        failed = problem_response(
            request, status=401, code=exc.code, title=exc.title, detail=exc.detail
        )
        clear_session_cookies(failed, settings)
        return failed
    return _login_json(
        request, issued, mfa_enabled=await get_mfa_service(request).is_enabled(issued.user.id)
    )


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, service: Service) -> Response:
    await service.logout(
        refresh_token=request.cookies.get(REFRESH_COOKIE),
        csrf_header=request.headers.get(CSRF_HEADER),
    )
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(out, request.app.state.settings)
    return out


# ------------------------------------------------------------------------- recuperación


@auth_router.post(
    "/password/forgot", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedResponse
)
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, service: Service
) -> AcceptedResponse:
    await service.forgot_password(email=body.email, ip=client_ip(request))
    return AcceptedResponse(message=FORGOT_ACCEPTED)


@auth_router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest, service: Service) -> Response:
    await service.reset_password(token=body.token, new_password=body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------------------------ MFA


@auth_router.post("/mfa/totp/enroll", response_model=MfaEnrollResponse)
async def mfa_enroll(ctx: CurrentAuth, mfa: Mfa) -> MfaEnrollResponse:
    enrollment = await mfa.enroll(ctx.user)
    return MfaEnrollResponse(secret=enrollment.secret, otpauth_uri=enrollment.otpauth_uri)


@auth_router.post("/mfa/totp/confirm", response_model=MfaConfirmResponse)
async def mfa_confirm(body: MfaConfirmRequest, ctx: CurrentAuth, mfa: Mfa) -> MfaConfirmResponse:
    return MfaConfirmResponse(recovery_codes=await mfa.confirm(ctx.user, body.code))


@auth_router.post("/mfa/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(body: MfaDisableRequest, ctx: CurrentAuth, mfa: Mfa) -> Response:
    await mfa.disable(ctx.user, password=body.password, code=body.code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------------- Google


@auth_router.get("/google/start")
async def google_start(
    request: Request,
    oidc: Oidc,
    next: Annotated[str | None, Query(max_length=200)] = None,  # noqa: A002
    fake_email: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    started = await oidc.start(next_path=next, hint=fake_email)
    out = RedirectResponse(started.url, status_code=status.HTTP_302_FOUND)
    out.set_cookie(
        OIDC_COOKIE,
        started.state,
        max_age=OIDC_COOKIE_TTL_S,
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="lax",
        path=REFRESH_PATH,
    )
    return out


@auth_router.get("/google/callback")
async def google_callback(
    request: Request,
    oidc: Oidc,
    service: Service,
    code: Annotated[str, Query(max_length=2000)],
    state: Annotated[str, Query(max_length=200)],
) -> Response:
    settings = request.app.state.settings
    web = settings.public_web_url
    try:
        result = await oidc.callback(
            code=code, state=state, state_cookie=request.cookies.get(OIDC_COOKIE)
        )
        outcome = await service.login_with_user(
            result.user, ip=client_ip(request), user_agent=request.headers.get("user-agent")
        )
    except ApiError as exc:
        if exc.status not in (401, 429):
            raise
        log.warning("oidc_callback_failed", code=exc.code)
        failed = RedirectResponse(f"{web}/login?error=oidc_failed", status_code=302)
        failed.delete_cookie(OIDC_COOKIE, path=REFRESH_PATH)
        return failed

    if isinstance(outcome, MfaChallenge):
        out = RedirectResponse(f"{web}/login/mfa", status_code=status.HTTP_302_FOUND)
        out.set_cookie(
            MFA_COOKIE,
            outcome.token,
            max_age=MFA_COOKIE_TTL_S,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="lax",
            path=REFRESH_PATH,
        )
    else:
        out = RedirectResponse(f"{web}{result.next_path}", status_code=status.HTTP_302_FOUND)
        set_session_cookies(out, settings, outcome)
    out.delete_cookie(OIDC_COOKIE, path=REFRESH_PATH)
    return out


# ------------------------------------------------------------------------------------ /me


@me_router.get("", response_model=UserOut)
async def get_me(ctx: CurrentAuth) -> UserOut:
    return _user_out(ctx.user, mfa_enabled=ctx.mfa_enabled)


@me_router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(ctx: CurrentAuth, service: Service) -> list[SessionOut]:
    rows = await service.list_sessions(ctx)
    return [
        SessionOut(
            id=r.id,
            user_agent=r.user_agent,
            ip=str(r.ip) if r.ip else None,
            created_at=r.created_at,
            expires_at=r.expires_at,
            current=current,
        )
        for r, current in rows
    ]


@me_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: uuid.UUID, ctx: CurrentAuth, service: Service) -> Response:
    await service.revoke_session(ctx, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
