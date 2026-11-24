"""Utilidades de pruebas para la biblioteca de medios (compartidas con tests/security)."""

import uuid
from typing import Any

import asyncpg
from harness import ApiHarness
from klick.media.service import MediaService
from klick.orgs.permissions import ALL_PERMISSIONS
from klick.orgs.principal import Principal
from klick.storage.service import StorageService
from sqlalchemy.engine import make_url


def principal(org_id: str | uuid.UUID, user_id: str | uuid.UUID, role: str = "owner") -> Principal:
    return Principal(
        user_id=uuid.UUID(str(user_id)),
        org_id=uuid.UUID(str(org_id)),
        role_key=role,
        permissions=ALL_PERMISSIONS,
        origin="web",
    )


def media(api: ApiHarness) -> MediaService:
    service: MediaService = api.app.state.media_service
    return service


def storage_service(api: ApiHarness) -> StorageService:
    service: StorageService = api.app.state.storage_service
    return service


async def sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    """Consulta con el rol dueño (sin RLS): solo para preparar y verificar estado en pruebas."""
    dsn = make_url(api.db_urls["owner"]).set(drivername="postgresql")
    conn = await asyncpg.connect(dsn.render_as_string(hide_password=False))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def make_object(
    api: ApiHarness,
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    *,
    ready: bool = True,
    data: bytes = b"video-bytes",
) -> uuid.UUID:
    """Reserva un objeto del almacén y, si ``ready``, sube un archivo real y lo confirma."""
    org = uuid.UUID(str(org_id))
    key = f"org/{org}/assets/{uuid.uuid4()}/original"
    svc = storage_service(api)
    obj = await svc.register_upload(
        org, key=key, mime="video/mp4", created_by=uuid.UUID(str(user_id))
    )
    if ready:
        await api.app.state.storage.put_bytes(key, data, "video/mp4")
        await svc.mark_ready(org, obj.id)
    return obj.id


async def make_asset(
    api: ApiHarness,
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    *,
    title: str = "Directo de ajedrez",
    kind: str = "video",
    status: str = "uploading",
    project_id: uuid.UUID | None = None,
    folder_id: uuid.UUID | None = None,
    duration_ms: int = 60_000,
    **extra: Any,
) -> uuid.UUID:
    """Crea un asset por el servicio y lo lleva al estado pedido por transiciones reales."""
    p = principal(org_id, user_id)
    obj = await make_object(api, org_id, user_id, ready=status != "uploading")
    asset = await media(api).create_asset(
        p,
        kind=kind,
        title=title,
        storage_object_id=obj,
        original_filename=f"{title}.mp4",
        project_id=project_id,
        folder_id=folder_id,
        **extra,
    )
    if status in ("processing", "ready", "failed"):
        await media(api).transition_asset(p.org_id, asset.id, "processing")
    if status == "ready":
        await media(api).transition_asset(
            p.org_id, asset.id, "ready", duration_ms=duration_ms, width=1920, height=1080, fps=30
        )
    if status == "failed":
        await media(api).transition_asset(p.org_id, asset.id, "failed", error={"code": "corrupt"})
    return asset.id
