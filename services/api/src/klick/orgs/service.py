"""Casos de uso de organizaciones, membresías, invitaciones y tokens de API."""

import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.exc import IntegrityError

from klick.core.audit import record_audit
from klick.core.config import Settings
from klick.core.db import Database, set_context
from klick.core.email import Email, EmailSender
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.identity.models import User
from klick.identity.security import hash_token
from klick.orgs import repo
from klick.orgs.models import ApiToken, Invitation, Membership, Organization, Role
from klick.orgs.permissions import ALL_PERMISSIONS
from klick.orgs.principal import Origin, Principal, forbidden

log = structlog.get_logger("klick.orgs")

INVITATION_TTL = timedelta(days=7)
TOKEN_PREFIX = "klk_"  # noqa: S105 - prefijo público, no un secreto
TOKEN_TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class OrgView:
    org: Organization
    role_key: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class MemberView:
    membership: Membership
    user: User
    role: Role


@dataclass(frozen=True)
class CreatedToken:
    token: ApiToken
    raw: str


def _now() -> datetime:
    return datetime.now(UTC)


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "org"


async def _audit(
    s: Any,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    diff: dict[str, Any] | None = None,
) -> None:
    """Auditoría de una acción de organización, en la misma transacción del cambio."""
    await record_audit(
        s,
        action=action,
        actor_type="api_token" if principal.origin == "api_token" else "user",
        actor_id=principal.user_id,
        org_id=principal.org_id,
        resource_type=resource_type,
        resource_id=resource_id,
        diff=diff,
    )


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Resource not found")


def _invalid_invitation() -> ApiError:
    return ApiError(
        400, "invalid_token", "Invalid token", "La invitación no es válida o ya expiró."
    )


class OrgService:
    def __init__(self, db: Database, email: EmailSender, settings: Settings) -> None:
        self._db = db
        self._email = email
        self._s = settings

    # --------------------------------------------------------------- organizaciones

    async def create_org(self, user: User, *, name: str, slug: str | None) -> OrgView:
        base = slugify(slug or name)
        for attempt in range(6):
            candidate = base if attempt == 0 else f"{base[:35]}-{secrets.token_hex(2)}"
            org_id = new_id()
            try:
                async with self._db.tenant_session(org_id, user.id) as s:
                    owner = await repo.get_system_role(s, "owner")
                    if owner is None:
                        raise ApiError(
                            500, "roles_not_seeded", "Roles not seeded", "Ejecuta pnpm db:seed."
                        )
                    s.add(
                        Organization(
                            id=org_id,
                            name=name.strip(),
                            slug=candidate,
                            created_by=user.id,
                            settings={"locale": user.locale, "timezone": user.timezone},
                        )
                    )
                    await s.flush()
                    s.add(
                        Membership(
                            id=new_id(),
                            org_id=org_id,
                            user_id=user.id,
                            role_id=owner.id,
                            status="active",
                        )
                    )
                    await s.flush()
                    org = await repo.get_org(s, org_id)
                    perms = await repo.role_permissions(s, owner.id)
                    assert org is not None  # noqa: S101 - recién insertada en esta transacción
                    await record_audit(
                        s,
                        action="org.created",
                        actor_type="user",
                        actor_id=user.id,
                        org_id=org_id,
                        resource_type="organization",
                        resource_id=org_id,
                        diff={"name": org.name, "slug": org.slug},
                    )
            except IntegrityError:
                if slug is not None:  # el usuario pidió un slug concreto y ya existe
                    raise ApiError(
                        409, "slug_taken", "Slug taken", "Ese identificador ya está en uso."
                    ) from None
                continue
            log.info("org_created", org_id=str(org_id), user_id=str(user.id))
            return OrgView(org=org, role_key="owner", permissions=perms)
        raise ApiError(
            409, "slug_taken", "Slug taken", "No se pudo generar un identificador único."
        )

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrgView]:
        async with self._db.context_session(user_id=user_id) as s:
            rows = await repo.list_user_memberships(s, user_id)
            out = []
            for _, org, role in rows:
                out.append(OrgView(org, role.key, await repo.role_permissions(s, role.id)))
            return out

    async def principal_for(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        origin: Origin = "web",
        session_id: uuid.UUID | None = None,
    ) -> Principal | None:
        """Principal de un usuario en una organización, o None si no es miembro activo."""
        async with self._db.tenant_session(org_id, user_id) as s:
            found = await repo.get_membership(s, org_id, user_id)
            if found is None or found[0].status != "active":
                return None
            membership, role = found
            perms = await repo.role_permissions(s, role.id)
            return Principal(
                user_id=user_id,
                org_id=org_id,
                role_key=role.key,
                permissions=perms,
                origin=origin,
                session_id=session_id,
            )

    async def get_org(self, principal: Principal) -> OrgView:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            org = await repo.get_org(s, principal.org_id)
            if org is None:
                raise _not_found()
            return OrgView(org, principal.role_key, principal.permissions)

    async def update_org(
        self, principal: Principal, *, name: str | None, settings: dict[str, Any] | None
    ) -> OrgView:
        principal.require("org:manage")
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            org = await repo.get_org(s, principal.org_id)
            if org is None:
                raise _not_found()
            if name is not None:
                org.name = name.strip()
            if settings:
                org.settings = {**org.settings, **settings}
            await _audit(
                s,
                principal,
                "org.updated",
                "organization",
                principal.org_id,
                {"name": name, "settings": sorted(settings or {})},
            )
            return OrgView(org, principal.role_key, principal.permissions)

    # -------------------------------------------------------------------- miembros

    async def list_members(self, principal: Principal) -> list[MemberView]:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            return [MemberView(m, u, r) for m, u, r in await repo.list_members(s, principal.org_id)]

    async def _grantable_role(self, s: Any, principal: Principal, role_key: str) -> Role:
        role = await repo.get_system_role(s, role_key)
        if role is None:
            raise ApiError(422, "unknown_role", "Unknown role", "El rol no existe.")
        if not await repo.role_permissions(s, role.id) <= principal.permissions:
            # Nadie puede otorgar un rol con permisos que no tiene (escalada de privilegios).
            raise forbidden()
        return role

    async def change_role(
        self, principal: Principal, membership_id: uuid.UUID, role_key: str
    ) -> MemberView:
        principal.require("members:manage")
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await repo.lock_org_memberships(s, principal.org_id)
            found = await repo.get_membership_by_id(s, principal.org_id, membership_id)
            if found is None:
                raise _not_found()
            membership, current_role = found
            if not await repo.role_permissions(s, current_role.id) <= principal.permissions:
                raise forbidden()  # un admin no puede degradar a un owner
            new_role = await self._grantable_role(s, principal, role_key)
            if (
                current_role.key == "owner"
                and new_role.key != "owner"
                and membership.status == "active"
                and await repo.count_active_owners(s, principal.org_id) <= 1
            ):
                raise ApiError(
                    409,
                    "last_owner",
                    "Last owner",
                    "La organización debe conservar un propietario.",
                )
            old_role_key = current_role.key
            membership.role_id = new_role.id
            await s.flush()
            await _audit(
                s,
                principal,
                "member.role_changed",
                "membership",
                membership.id,
                {"user_id": str(membership.user_id), "from": old_role_key, "to": new_role.key},
            )
            user = await s.get(User, membership.user_id)
            assert user is not None  # noqa: S101 - FK garantiza su existencia
            log.info(
                "member_role_changed",
                org_id=str(principal.org_id),
                actor=str(principal.user_id),
                target=str(membership.user_id),
                role=role_key,
            )
            return MemberView(membership, user, new_role)

    async def remove_member(self, principal: Principal, membership_id: uuid.UUID) -> None:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await repo.lock_org_memberships(s, principal.org_id)
            found = await repo.get_membership_by_id(s, principal.org_id, membership_id)
            if found is None:
                raise _not_found()
            membership, role = found
            is_self = membership.user_id == principal.user_id
            if not is_self:
                principal.require("members:manage")
                if not await repo.role_permissions(s, role.id) <= principal.permissions:
                    raise forbidden()
            if (
                role.key == "owner"
                and membership.status == "active"
                and await repo.count_active_owners(s, principal.org_id) <= 1
            ):
                raise ApiError(
                    409,
                    "last_owner",
                    "Last owner",
                    "La organización debe conservar un propietario.",
                )
            await s.delete(membership)
            await _audit(
                s,
                principal,
                "member.removed",
                "membership",
                membership.id,
                {"user_id": str(membership.user_id), "role": role.key, "self": is_self},
            )
            log.info(
                "member_removed",
                org_id=str(principal.org_id),
                actor=str(principal.user_id),
                target=str(membership.user_id),
            )

    # ----------------------------------------------------------------- invitaciones

    async def invite(
        self, principal: Principal, *, email: str, role_key: str
    ) -> tuple[Invitation, Role]:
        principal.require("members:manage")
        email = email.strip().lower()
        raw = secrets.token_urlsafe(32)
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            role = await self._grantable_role(s, principal, role_key)
            org = await repo.get_org(s, principal.org_id)
            inviter = await s.get(User, principal.user_id)
            assert org is not None and inviter is not None  # noqa: S101
            inviter_name = inviter.display_name
            for m, u, _ in await repo.list_members(s, principal.org_id):
                if u.email.lower() == email and m.status == "active":
                    raise ApiError(
                        409, "already_member", "Already a member", "Esa persona ya es miembro."
                    )
            now = _now()
            for old in await repo.pending_invitations_for_email(s, principal.org_id, email):
                old.revoked_at = now  # una sola invitación vigente por correo
            invitation = Invitation(
                id=new_id(),
                org_id=principal.org_id,
                email=email,
                role_id=role.id,
                token_hash=hash_token(raw),
                invited_by=principal.user_id,
                expires_at=now + INVITATION_TTL,
            )
            s.add(invitation)
            await s.flush()
            await _audit(
                s,
                principal,
                "invitation.created",
                "invitation",
                invitation.id,
                {"email": email, "role": role_key},
            )
            org_name = org.name
        await self._email.send(
            Email(
                to=email,
                subject=f"{inviter_name} te invitó a {org_name} en Klick",
                body=(
                    f"{inviter_name} te invitó a unirte a {org_name} como {role.name}.\n\n"
                    f"Acepta la invitación (inicia sesión con este correo):\n"
                    f"{self._s.public_web_url}/invitations/accept?token={raw}\n\n"
                    "La invitación vence en 7 días."
                ),
            )
        )
        log.info("invitation_created", org_id=str(principal.org_id), role=role_key)
        return invitation, role

    async def list_invitations(self, principal: Principal) -> list[tuple[Invitation, Role]]:
        principal.require("members:manage")
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            return await repo.list_pending_invitations(s, principal.org_id, _now())

    async def revoke_invitation(self, principal: Principal, invitation_id: uuid.UUID) -> None:
        principal.require("members:manage")
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            inv = await repo.get_invitation(s, principal.org_id, invitation_id)
            if inv is None or inv.accepted_at is not None:
                raise _not_found()
            inv.revoked_at = _now()
            await _audit(s, principal, "invitation.revoked", "invitation", inv.id)

    async def accept_invitation(self, user: User, token: str) -> OrgView:
        if user.email_verified_at is None:
            raise ApiError(
                403,
                "email_not_verified",
                "Email not verified",
                "Verifica tu correo antes de aceptar la invitación.",
            )
        now = _now()
        token_hash = hash_token(token)
        async with self._db.context_session(invite_token_hash=token_hash, user_id=user.id) as s:
            inv = await repo.get_invitation_by_hash(s, token_hash)
            if (
                inv is None
                or inv.accepted_at is not None
                or inv.revoked_at is not None
                or inv.expires_at <= now
            ):
                raise _invalid_invitation()
            if inv.email.lower() != user.email.lower():
                raise forbidden()
            await set_context(s, org_id=inv.org_id)
            existing = await repo.get_membership(s, inv.org_id, user.id)
            if existing is not None and existing[0].status == "suspended":
                raise forbidden()
            if existing is None:
                s.add(
                    Membership(
                        id=new_id(),
                        org_id=inv.org_id,
                        user_id=user.id,
                        role_id=inv.role_id,
                        status="active",
                    )
                )
            inv.accepted_at = now
            await s.flush()
            await record_audit(
                s,
                action="invitation.accepted",
                actor_type="user",
                actor_id=user.id,
                org_id=inv.org_id,
                resource_type="invitation",
                resource_id=inv.id,
            )
            org = await repo.get_org(s, inv.org_id)
            role = await repo.get_role(s, inv.role_id)
            assert org is not None and role is not None  # noqa: S101
            perms = await repo.role_permissions(s, role.id)
            log.info("invitation_accepted", org_id=str(inv.org_id), user_id=str(user.id))
            return OrgView(org, role.key, perms)

    # ------------------------------------------------------------------ tokens de API

    async def create_token(
        self,
        principal: Principal,
        *,
        name: str,
        scopes: list[str],
        expires_in_days: int | None,
    ) -> CreatedToken:
        if principal.origin != "web":
            raise forbidden()  # un token no puede crear más tokens
        unknown = sorted(set(scopes) - ALL_PERMISSIONS)
        if unknown:
            raise ApiError(
                422,
                "unknown_scope",
                "Unknown scope",
                f"Alcances desconocidos: {', '.join(unknown)}",
            )
        excess = sorted(set(scopes) - principal.permissions)
        if excess:
            raise forbidden(excess)  # no se pueden otorgar permisos que no se tienen
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires = _now() + timedelta(days=expires_in_days) if expires_in_days else None
        token = ApiToken(
            id=new_id(),
            org_id=principal.org_id,
            user_id=principal.user_id,
            name=name.strip(),
            prefix=raw[:8],
            token_hash=hash_token(raw),
            scopes=sorted(set(scopes)),
            expires_at=expires,
        )
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            s.add(token)
            await s.flush()
            await _audit(
                s,
                principal,
                "api_token.created",
                "api_token",
                token.id,
                {"name": token.name, "scopes": token.scopes, "expires_at": str(token.expires_at)},
            )
        log.info("api_token_created", org_id=str(principal.org_id), token_id=str(token.id))
        return CreatedToken(token=token, raw=raw)

    async def list_tokens(self, principal: Principal) -> list[ApiToken]:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            return await repo.list_tokens(s, principal.org_id, principal.user_id)

    async def revoke_token(self, principal: Principal, token_id: uuid.UUID) -> None:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            token = await repo.get_token(s, principal.org_id, token_id)
            if token is None or token.revoked_at is not None:
                raise _not_found()
            if token.user_id != principal.user_id:
                principal.require("members:manage")
            token.revoked_at = _now()
            await _audit(s, principal, "api_token.revoked", "api_token", token.id)

    async def authenticate_token(self, raw: str) -> Principal | None:
        """Resuelve un token de API a un Principal. Sus permisos nunca exceden los del usuario."""
        if not raw.startswith(TOKEN_PREFIX) or len(raw) > 200:
            return None
        token_hash = hash_token(raw)
        now = _now()
        async with self._db.context_session(token_hash=token_hash) as s:
            token = await repo.get_token_by_hash(s, token_hash)
            if token is None or token.revoked_at is not None:
                return None
            if token.expires_at is not None and token.expires_at <= now:
                return None
            await set_context(s, org_id=token.org_id, user_id=token.user_id)
            found = await repo.get_membership(s, token.org_id, token.user_id)
            if found is None or found[0].status != "active":
                return None
            user = await s.get(User, token.user_id)
            if user is None or not user.is_active:
                return None
            role = found[1]
            user_perms = await repo.role_permissions(s, role.id)
            if token.last_used_at is None or now - token.last_used_at > TOKEN_TOUCH_INTERVAL:
                token.last_used_at = now
            return Principal(
                user_id=token.user_id,
                org_id=token.org_id,
                role_key=role.key,
                permissions=frozenset(token.scopes) & user_perms,
                origin="api_token",
                token_id=token.id,
            )
