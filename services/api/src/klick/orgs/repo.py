"""Acceso a datos de organizaciones. Solo lo usa orgs.service."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from klick.identity.models import User
from klick.orgs.models import (
    ApiToken,
    Invitation,
    Membership,
    Organization,
    Role,
    RolePermission,
)


async def get_system_role(db: AsyncSession, key: str) -> Role | None:
    return (
        await db.execute(select(Role).where(Role.key == key, Role.org_id.is_(None)))
    ).scalar_one_or_none()


async def get_role(db: AsyncSession, role_id: uuid.UUID) -> Role | None:
    return await db.get(Role, role_id)


async def role_permissions(db: AsyncSession, role_id: uuid.UUID) -> frozenset[str]:
    rows = await db.execute(
        select(RolePermission.permission_key).where(RolePermission.role_id == role_id)
    )
    return frozenset(rows.scalars())


async def get_org(db: AsyncSession, org_id: uuid.UUID) -> Organization | None:
    return (
        await db.execute(
            select(Organization).where(Organization.id == org_id, Organization.deleted_at.is_(None))
        )
    ).scalar_one_or_none()


async def list_user_memberships(
    db: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Membership, Organization, Role]]:
    rows = await db.execute(
        select(Membership, Organization, Role)
        .join(Organization, Organization.id == Membership.org_id)
        .join(Role, Role.id == Membership.role_id)
        .where(
            Membership.user_id == user_id,
            Membership.status == "active",
            Organization.deleted_at.is_(None),
        )
        .order_by(Organization.name)
    )
    return [(m, o, r) for m, o, r in rows.all()]


async def get_membership(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Membership, Role] | None:
    row = (
        await db.execute(
            select(Membership, Role)
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.org_id == org_id, Membership.user_id == user_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def get_membership_by_id(
    db: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID, *, for_update: bool = False
) -> tuple[Membership, Role] | None:
    stmt = (
        select(Membership, Role)
        .join(Role, Role.id == Membership.role_id)
        .where(Membership.org_id == org_id, Membership.id == membership_id)
    )
    if for_update:
        stmt = stmt.with_for_update(of=Membership)
    row = (await db.execute(stmt)).first()
    return (row[0], row[1]) if row else None


async def list_members(db: AsyncSession, org_id: uuid.UUID) -> list[tuple[Membership, User, Role]]:
    rows = await db.execute(
        select(Membership, User, Role)
        .join(User, User.id == Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at)
    )
    return [(m, u, r) for m, u, r in rows.all()]


async def count_active_owners(db: AsyncSession, org_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Membership)
                .join(Role, Role.id == Membership.role_id)
                .where(
                    Membership.org_id == org_id,
                    Membership.status == "active",
                    Role.key == "owner",
                    Role.org_id.is_(None),
                )
            )
        ).scalar_one()
    )


async def lock_org_memberships(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Serializa cambios de roles/altas/bajas de una organización (guardia del último owner)."""
    await db.execute(select(Membership.id).where(Membership.org_id == org_id).with_for_update())


# ------------------------------------------------------------------------------ invitaciones


async def get_invitation_by_hash(db: AsyncSession, token_hash: str) -> Invitation | None:
    return (
        await db.execute(select(Invitation).where(Invitation.token_hash == token_hash))
    ).scalar_one_or_none()


async def list_pending_invitations(
    db: AsyncSession, org_id: uuid.UUID, now: datetime
) -> list[tuple[Invitation, Role]]:
    rows = await db.execute(
        select(Invitation, Role)
        .join(Role, Role.id == Invitation.role_id)
        .where(
            Invitation.org_id == org_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc())
    )
    return [(i, r) for i, r in rows.all()]


async def get_invitation(
    db: AsyncSession, org_id: uuid.UUID, invitation_id: uuid.UUID
) -> Invitation | None:
    return (
        await db.execute(
            select(Invitation).where(Invitation.org_id == org_id, Invitation.id == invitation_id)
        )
    ).scalar_one_or_none()


async def pending_invitations_for_email(
    db: AsyncSession, org_id: uuid.UUID, email: str
) -> list[Invitation]:
    rows = await db.execute(
        select(Invitation).where(
            Invitation.org_id == org_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    )
    return list(rows.scalars())


# ------------------------------------------------------------------------------ tokens de API


async def get_token_by_hash(db: AsyncSession, token_hash: str) -> ApiToken | None:
    return (
        await db.execute(select(ApiToken).where(ApiToken.token_hash == token_hash))
    ).scalar_one_or_none()


async def list_tokens(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None
) -> list[ApiToken]:
    stmt = select(ApiToken).where(ApiToken.org_id == org_id, ApiToken.revoked_at.is_(None))
    if user_id is not None:
        stmt = stmt.where(ApiToken.user_id == user_id)
    return list((await db.execute(stmt.order_by(ApiToken.created_at.desc()))).scalars())


async def get_token(db: AsyncSession, org_id: uuid.UUID, token_id: uuid.UUID) -> ApiToken | None:
    return (
        await db.execute(select(ApiToken).where(ApiToken.org_id == org_id, ApiToken.id == token_id))
    ).scalar_one_or_none()
