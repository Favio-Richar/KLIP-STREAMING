"""Endpoints de organizaciones, miembros, invitaciones y tokens de API (docs/05 §2)."""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from klick.core.idempotency import Idem
from klick.identity.deps import CurrentAuth
from klick.orgs.deps import CurrentPrincipal, Orgs, PathPrincipal
from klick.orgs.schemas import (
    InvitationCreate,
    InvitationOut,
    MemberOut,
    MemberUpdate,
    OrgCreate,
    OrgOut,
    OrgUpdate,
    TokenCreate,
    TokenCreated,
    TokenOut,
)
from klick.orgs.service import MemberView, OrgView

orgs_router = APIRouter(prefix="/orgs", tags=["orgs"])
members_router = APIRouter(prefix="/members", tags=["members"])
invitations_router = APIRouter(prefix="/invitations", tags=["invitations"])
tokens_router = APIRouter(prefix="/me/api-tokens", tags=["api-tokens"])


def _org_out(view: OrgView) -> OrgOut:
    return OrgOut(
        id=view.org.id,
        name=view.org.name,
        slug=view.org.slug,
        role=view.role_key,
        permissions=sorted(view.permissions),
        settings=view.org.settings,
        created_at=view.org.created_at,
    )


def _member_out(view: MemberView) -> MemberOut:
    return MemberOut(
        id=view.membership.id,
        user_id=view.user.id,
        email=view.user.email,
        display_name=view.user.display_name,
        role=view.role.key,
        status=view.membership.status,
        joined_at=view.membership.created_at,
    )


# ------------------------------------------------------------------------- organizaciones


@orgs_router.get("", response_model=list[OrgOut])
async def list_orgs(ctx: CurrentAuth, orgs: Orgs) -> list[OrgOut]:
    return [_org_out(v) for v in await orgs.list_for_user(ctx.user.id)]


@orgs_router.post("", status_code=status.HTTP_201_CREATED, response_model=OrgOut)
async def create_org(body: OrgCreate, ctx: CurrentAuth, orgs: Orgs) -> OrgOut:
    return _org_out(await orgs.create_org(ctx.user, name=body.name, slug=body.slug))


@orgs_router.get("/{org_id}", response_model=OrgOut)
async def get_org(principal: PathPrincipal, orgs: Orgs) -> OrgOut:
    return _org_out(await orgs.get_org(principal))


@orgs_router.patch("/{org_id}", response_model=OrgOut)
async def update_org(body: OrgUpdate, principal: PathPrincipal, orgs: Orgs) -> OrgOut:
    settings = body.settings.model_dump(exclude_none=True) if body.settings else None
    return _org_out(await orgs.update_org(principal, name=body.name, settings=settings))


# ----------------------------------------------------------------------------- miembros


@members_router.get("", response_model=list[MemberOut])
async def list_members(principal: CurrentPrincipal, orgs: Orgs) -> list[MemberOut]:
    return [_member_out(v) for v in await orgs.list_members(principal)]


@members_router.patch("/{member_id}", response_model=MemberOut)
async def change_member_role(
    member_id: uuid.UUID, body: MemberUpdate, principal: CurrentPrincipal, orgs: Orgs
) -> MemberOut:
    return _member_out(await orgs.change_role(principal, member_id, body.role))


@members_router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(member_id: uuid.UUID, principal: CurrentPrincipal, orgs: Orgs) -> Response:
    await orgs.remove_member(principal, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------------- invitaciones


@invitations_router.get("", response_model=list[InvitationOut])
async def list_invitations(principal: CurrentPrincipal, orgs: Orgs) -> list[InvitationOut]:
    return [
        InvitationOut(
            id=i.id, email=i.email, role=r.key, expires_at=i.expires_at, created_at=i.created_at
        )
        for i, r in await orgs.list_invitations(principal)
    ]


@invitations_router.post("", status_code=status.HTTP_201_CREATED, response_model=InvitationOut)
async def create_invitation(
    body: InvitationCreate, request: Request, principal: CurrentPrincipal, orgs: Orgs, idem: Idem
) -> JSONResponse:
    async def produce() -> tuple[int, Any]:
        inv, role = await orgs.invite(principal, email=body.email, role_key=body.role)
        out = InvitationOut(
            id=inv.id,
            email=inv.email,
            role=role.key,
            expires_at=inv.expires_at,
            created_at=inv.created_at,
        )
        return 201, out.model_dump(mode="json")

    return await idem.run(
        request, principal.org_id, "invitations.create", body.model_dump(mode="json"), produce
    )


@invitations_router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID, principal: CurrentPrincipal, orgs: Orgs
) -> Response:
    await orgs.revoke_invitation(principal, invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@invitations_router.post("/{token}/accept", response_model=OrgOut)
async def accept_invitation(token: str, ctx: CurrentAuth, orgs: Orgs) -> OrgOut:
    return _org_out(await orgs.accept_invitation(ctx.user, token))


# ------------------------------------------------------------------------ tokens de API


@tokens_router.get("", response_model=list[TokenOut])
async def list_tokens(principal: CurrentPrincipal, orgs: Orgs) -> list[TokenOut]:
    return [TokenOut.model_validate(t) for t in await orgs.list_tokens(principal)]


@tokens_router.post("", status_code=status.HTTP_201_CREATED, response_model=TokenCreated)
async def create_token(body: TokenCreate, principal: CurrentPrincipal, orgs: Orgs) -> TokenCreated:
    created = await orgs.create_token(
        principal, name=body.name, scopes=body.scopes, expires_in_days=body.expires_in_days
    )
    return TokenCreated(**TokenOut.model_validate(created.token).model_dump(), token=created.raw)


@tokens_router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: uuid.UUID, principal: CurrentPrincipal, orgs: Orgs) -> Response:
    await orgs.revoke_token(principal, token_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
