"""Dependencias de autenticación con organización: producen el Principal de cada petición."""

import uuid
from typing import Annotated

from fastapi import Depends, Query, Request

from klick.core.errors import ApiError
from klick.identity.deps import current_auth, get_identity_service
from klick.identity.service import IdentityService, unauthenticated
from klick.orgs.principal import Principal, forbidden
from klick.orgs.service import OrgService

ORG_HEADER = "X-Org-ID"


def get_org_service(request: Request) -> OrgService:
    service: OrgService = request.app.state.org_service
    return service


def _header_org(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(ORG_HEADER)
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ApiError(
            400, "invalid_org_id", "Invalid organization id", f"{ORG_HEADER} debe ser un UUID."
        ) from None


async def resolve_principal(
    request: Request,
    orgs: OrgService,
    identity: IdentityService,
    *,
    org_id: uuid.UUID | None = None,
) -> Principal:
    """Token de API (``Authorization: Bearer klk_...``) o sesión web (cookie + CSRF + X-Org-ID).

    ``org_id`` fuerza la organización (rutas con ``/orgs/{id}``); si no, se usa la cabecera.
    """
    wanted = org_id or _header_org(request)
    authz = request.headers.get("authorization")
    if authz:
        scheme, _, credential = authz.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            raise unauthenticated("invalid_token")
        principal = await orgs.authenticate_token(credential.strip())
        if principal is None:
            raise unauthenticated("invalid_token")
        if wanted is not None and wanted != principal.org_id:
            raise forbidden()
        return principal

    ctx = await current_auth(request, identity)
    if wanted is None:
        raise ApiError(
            400,
            "org_required",
            "Organization required",
            f"Indica la organización con la cabecera {ORG_HEADER}.",
        )
    principal = await orgs.principal_for(ctx.user.id, wanted, session_id=ctx.session_id)
    if principal is None:
        raise forbidden()  # no revela si la organización existe
    return principal


async def current_principal(
    request: Request,
    orgs: Annotated[OrgService, Depends(get_org_service)],
    identity: Annotated[IdentityService, Depends(get_identity_service)],
) -> Principal:
    return await resolve_principal(request, orgs, identity)


async def path_principal(
    org_id: uuid.UUID,
    request: Request,
    orgs: Annotated[OrgService, Depends(get_org_service)],
    identity: Annotated[IdentityService, Depends(get_identity_service)],
) -> Principal:
    return await resolve_principal(request, orgs, identity, org_id=org_id)


async def stream_principal(
    request: Request,
    orgs: Annotated[OrgService, Depends(get_org_service)],
    identity: Annotated[IdentityService, Depends(get_identity_service)],
    org_id: Annotated[uuid.UUID | None, Query(description="Alternativa a X-Org-ID")] = None,
) -> Principal:
    """Para flujos SSE: EventSource no puede enviar cabeceras, así que admite ``?org_id=``."""
    return await resolve_principal(request, orgs, identity, org_id=org_id)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
PathPrincipal = Annotated[Principal, Depends(path_principal)]
StreamPrincipal = Annotated[Principal, Depends(stream_principal)]
Orgs = Annotated[OrgService, Depends(get_org_service)]
