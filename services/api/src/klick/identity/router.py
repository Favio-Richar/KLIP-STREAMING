"""Endpoints de identidad: /auth/* y /me (docs/05 §2)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from klick.core.errors import ApiError, problem_response
from klick.identity.deps import (
    CSRF_HEADER,
    REFRESH_COOKIE,
    CurrentAuth,
    clear_session_cookies,
    client_ip,
    get_identity_service,
    set_session_cookies,
)
from klick.identity.models import User
from klick.identity.schemas import (
    AcceptedResponse,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
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
    unauthenticated,
)

Service = Annotated[IdentityService, Depends(get_identity_service)]

auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        locale=user.locale,
        timezone=user.timezone,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )


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


@auth_router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, service: Service
) -> LoginResponse:
    issued = await service.login(
        email=body.email,
        password=body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    set_session_cookies(response, request.app.state.settings, issued)
    return LoginResponse(user=_user_out(issued.user), csrf_token=issued.csrf_token)


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
    out = JSONResponse(
        LoginResponse(user=_user_out(issued.user), csrf_token=issued.csrf_token).model_dump(
            mode="json"
        )
    )
    set_session_cookies(out, settings, issued)
    return out


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, service: Service) -> Response:
    await service.logout(
        refresh_token=request.cookies.get(REFRESH_COOKIE),
        csrf_header=request.headers.get(CSRF_HEADER),
    )
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(out, request.app.state.settings)
    return out


@auth_router.post("/email/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: VerifyEmailRequest, service: Service) -> Response:
    await service.verify_email(body.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@me_router.get("", response_model=UserOut)
async def get_me(ctx: CurrentAuth) -> UserOut:
    return _user_out(ctx.user)


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
