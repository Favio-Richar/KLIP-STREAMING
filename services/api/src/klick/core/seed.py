"""Semillas del sistema (idempotentes): permisos y roles de sistema. ``pnpm db:seed``."""

import asyncio

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from klick.core.config import get_settings
from klick.core.db import Database
from klick.core.ids import new_id
from klick.orgs.models import Permission, Role, RolePermission
from klick.orgs.permissions import PERMISSIONS, ROLE_NAMES, ROLE_PERMISSIONS

log = structlog.get_logger("klick.seed")


async def seed_permissions_and_roles(session: AsyncSession) -> None:
    """Requiere un rol con BYPASSRLS (klick_system): los roles de sistema tienen org_id nulo."""
    existing = {p.key: p for p in (await session.execute(select(Permission))).scalars()}
    for key, description in PERMISSIONS.items():
        if key in existing:
            existing[key].description = description
        else:
            session.add(Permission(key=key, description=description))
    await session.flush()

    roles = {
        r.key: r
        for r in (await session.execute(select(Role).where(Role.org_id.is_(None)))).scalars()
    }
    for key, name in ROLE_NAMES.items():
        role = roles.get(key)
        if role is None:
            role = Role(id=new_id(), org_id=None, key=key, name=name, is_system=True)
            session.add(role)
            roles[key] = role
        else:
            role.name = name
            role.is_system = True
    await session.flush()

    for key, perms in ROLE_PERMISSIONS.items():
        role = roles[key]
        await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        session.add_all(RolePermission(role_id=role.id, permission_key=p) for p in sorted(perms))
    await session.flush()


async def main() -> None:
    settings = get_settings()
    if not settings.database_url_system:
        raise SystemExit("Defina DATABASE_URL_SYSTEM para sembrar datos")
    db = Database(settings.model_copy(update={"database_url": settings.database_url_system}))
    # Se reutiliza el motor de sistema como motor principal: la semilla solo usa system_session.
    try:
        async with db.global_session() as session:
            await seed_permissions_and_roles(session)
        log.info("seed_done", permissions=len(PERMISSIONS), roles=len(ROLE_NAMES))
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
