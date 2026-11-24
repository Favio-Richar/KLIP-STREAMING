"""Casos de uso de almacenamiento: registro, verificación, deduplicación y conteo de referencias."""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from klick.core.config import Settings
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.storage.backend import StorageBackend
from klick.storage.keys import InvalidKeyError, belongs_to_org, validate_key
from klick.storage.models import StorageObject

log = structlog.get_logger("klick.storage")


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Resource not found")


def _conflict(code: str, detail: str) -> ApiError:
    return ApiError(409, code, "Conflict", detail)


class StorageService:
    def __init__(self, db: Database, backend: StorageBackend, settings: Settings) -> None:
        self._db = db
        self._backend = backend
        self._s = settings

    # ------------------------------------------------------------------------- registro

    async def register_upload(
        self,
        org_id: uuid.UUID,
        *,
        key: str,
        mime: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> StorageObject:
        """Reserva el objeto (``uploading``) antes de que el cliente suba nada."""
        try:
            validate_key(key)
        except InvalidKeyError as exc:
            raise ApiError(400, "invalid_storage_key", "Invalid key", str(exc)) from None
        if not belongs_to_org(key, org_id):
            raise ApiError(
                400,
                "invalid_storage_key",
                "Invalid key",
                "La clave no pertenece a la organización.",
            )
        obj = StorageObject(
            id=new_id(),
            org_id=org_id,
            bucket=self._backend.bucket,
            key=key,
            mime=mime,
            status="uploading",
            created_by=created_by,
        )
        try:
            async with self._db.tenant_session(org_id) as s:
                s.add(obj)
                await s.flush()
        except IntegrityError:
            raise _conflict("storage_key_exists", "Ya existe un objeto con esa clave.") from None
        return obj

    async def get(self, org_id: uuid.UUID, object_id: uuid.UUID) -> StorageObject:
        async with self._db.tenant_session(org_id) as s:
            obj = (
                await s.execute(
                    select(StorageObject).where(
                        StorageObject.id == object_id, StorageObject.org_id == org_id
                    )
                )
            ).scalar_one_or_none()
            if obj is None or obj.status == "deleted":
                raise _not_found()
            return obj

    async def mark_ready(
        self, org_id: uuid.UUID, object_id: uuid.UUID, *, sha256: bytes | None = None
    ) -> StorageObject:
        """Confirma una subida: el objeto debe existir en el almacén; el tamaño es el real."""
        obj = await self.get(org_id, object_id)
        if obj.status not in ("uploading", "ready"):
            raise _conflict("storage_object_state", f"El objeto está en estado {obj.status}.")
        info = await self._backend.head(obj.key)
        if info is None:
            raise _conflict("storage_object_missing", "El objeto aún no está en el almacén.")
        if sha256 is not None and len(sha256) != 32:
            raise ApiError(422, "invalid_sha256", "Invalid sha256", "Se esperan 32 bytes.")
        async with self._db.tenant_session(org_id) as s:
            row = (
                await s.execute(
                    select(StorageObject).where(StorageObject.id == object_id).with_for_update()
                )
            ).scalar_one()
            row.size_bytes = info.size
            row.mime = row.mime or info.content_type
            if sha256 is not None:
                row.sha256 = sha256
            row.status = "ready"
            row.orphaned_at = None
            return row

    async def find_ready_by_sha256(self, org_id: uuid.UUID, sha256: bytes) -> StorageObject | None:
        """Deduplicación (ADR-005): solo dentro de la misma organización."""
        async with self._db.tenant_session(org_id) as s:
            return (
                await s.execute(
                    select(StorageObject)
                    .where(
                        StorageObject.org_id == org_id,
                        StorageObject.sha256 == sha256,
                        StorageObject.status.in_(("ready", "orphan")),
                    )
                    .order_by(StorageObject.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()

    # ------------------------------------------------------------------------ referencias

    async def add_ref(self, org_id: uuid.UUID, object_id: uuid.UUID) -> StorageObject:
        """+1 referencia. Un huérfano referenciado de nuevo vuelve a ``ready`` (sale de la gracia)."""
        async with self._db.tenant_session(org_id) as s:
            row = (
                await s.execute(
                    update(StorageObject)
                    .where(
                        StorageObject.id == object_id,
                        StorageObject.org_id == org_id,
                        StorageObject.status.in_(("ready", "orphan")),
                    )
                    .values(ref_count=StorageObject.ref_count + 1, status="ready", orphaned_at=None)
                    .returning(StorageObject)
                )
            ).scalar_one_or_none()
            if row is None:
                raise _conflict(
                    "storage_object_state", "El objeto no está disponible para referenciar."
                )
            return row

    async def release_ref(self, org_id: uuid.UUID, object_id: uuid.UUID) -> StorageObject:
        """-1 referencia. Al llegar a cero pasa a ``orphan``; el recolector lo borra tras la gracia."""
        async with self._db.tenant_session(org_id) as s:
            obj = (
                await s.execute(
                    select(StorageObject)
                    .where(StorageObject.id == object_id, StorageObject.org_id == org_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if obj is None or obj.status == "deleted":
                raise _not_found()
            obj.ref_count = max(obj.ref_count - 1, 0)
            if obj.ref_count == 0 and obj.status == "ready":
                obj.status = "orphan"
                obj.orphaned_at = datetime.now(UTC)
            return obj

    # ----------------------------------------------------------------------- URL firmadas

    async def presign_upload(self, org_id: uuid.UUID, object_id: uuid.UUID) -> str:
        obj = await self.get(org_id, object_id)
        if obj.status != "uploading":
            raise _conflict("storage_object_state", "Solo se sube un objeto en estado uploading.")
        return self._backend.presign_put(
            obj.key, expires_s=self._s.storage_url_ttl_s, content_type=obj.mime
        )

    async def presign_download(
        self, org_id: uuid.UUID, object_id: uuid.UUID, *, filename: str | None = None
    ) -> str:
        obj = await self.get(org_id, object_id)
        if obj.status != "ready":
            raise _conflict("storage_object_state", "El objeto aún no está listo.")
        return self._backend.presign_get(
            obj.key, expires_s=self._s.storage_url_ttl_s, filename=filename, content_type=None
        )
