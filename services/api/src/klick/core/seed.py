"""Semillas del sistema (idempotentes): permisos, roles y presets de render. ``pnpm db:seed``."""

import asyncio

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from klick.core.config import get_settings
from klick.core.db import Database
from klick.core.ids import new_id
from klick.orgs.models import Permission, Role, RolePermission
from klick.orgs.permissions import PERMISSIONS, ROLE_NAMES, ROLE_PERMISSIONS
from klick.render.models import RenderPresetRow
from klick.render.presets import SYSTEM_PRESETS

log = structlog.get_logger("klick.seed")


async def seed_render_presets(session: AsyncSession) -> None:
    """Presets de exportación del sistema (``org_id`` nulo). Se actualizan si cambian en el código."""
    existing = {
        p.key: p
        for p in (
            await session.execute(select(RenderPresetRow).where(RenderPresetRow.org_id.is_(None)))
        ).scalars()
    }
    for key, preset in SYSTEM_PRESETS.items():
        row = existing.get(key) or RenderPresetRow(id=new_id(), org_id=None, key=key)
        row.width, row.height, row.fps, row.crf = (
            preset.width,
            preset.height,
            preset.fps,
            preset.crf,
        )
        row.max_bitrate_kbps = preset.max_bitrate_kbps
        row.audio_bitrate_kbps = preset.audio_bitrate_kbps
        row.loudness_target_lufs = preset.loudness_target_lufs  # type: ignore[assignment]
        row.container, row.video_codec, row.audio_codec = (
            preset.container,
            preset.video_codec,
            preset.audio_codec,
        )
        row.is_system = True
        if key not in existing:
            session.add(row)
    await session.flush()


async def seed_permissions_and_roles(session: AsyncSession) -> None:
    """Permisos, roles de sistema y presets de render. Requiere un rol con BYPASSRLS
    (klick_system): los roles y presets de sistema tienen org_id nulo."""
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
    await seed_render_presets(session)


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
