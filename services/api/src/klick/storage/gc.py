"""Recolector de objetos huérfanos (ADR-005): borra del almacén lo que ya nadie referencia.

Corre en el scheduler con el rol de sistema (ve todas las organizaciones). Es seguro repetirlo:
borrar un objeto ausente no es un error y un fallo del almacén deja la fila para el siguiente ciclo.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select, update

from klick.core.config import Settings
from klick.core.db import Database
from klick.storage.backend import StorageBackend
from klick.storage.models import StorageObject

log = structlog.get_logger("klick.storage.gc")

BATCH = 100
DELETED_ROW_RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class GcReport:
    abandoned_uploads: int = 0
    deleted: int = 0
    failed: int = 0
    purged_rows: int = 0


async def collect_garbage(db: Database, backend: StorageBackend, settings: Settings) -> GcReport:
    now = datetime.now(UTC)
    stale_before = now - timedelta(hours=settings.storage_upload_stale_h)
    grace_before = now - timedelta(hours=settings.storage_orphan_grace_h)

    # 1) subidas que nunca se completaron: pasan a huérfanas y siguen el mismo plazo de gracia
    async with db.system_session() as s:
        abandoned = (
            await s.execute(
                update(StorageObject)
                .where(StorageObject.status == "uploading", StorageObject.created_at < stale_before)
                .values(status="orphan", orphaned_at=now)
                .returning(StorageObject.id)
            )
        ).all()

    # 2) huérfanos sin referencias y con la gracia cumplida
    deleted = failed = 0
    async with db.system_session() as s:
        victims = list(
            (
                await s.execute(
                    select(StorageObject)
                    .where(
                        StorageObject.status == "orphan",
                        StorageObject.ref_count == 0,
                        StorageObject.orphaned_at < grace_before,
                    )
                    .order_by(StorageObject.orphaned_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for obj in victims:
            try:
                await backend.delete(obj.key)
            except Exception:
                failed += 1
                log.exception("storage_delete_failed", object_id=str(obj.id))
                continue
            obj.status = "deleted"
            deleted += 1

    # 3) filas ya borradas hace tiempo: se purgan para que la tabla no crezca sin límite
    async with db.system_session() as s:
        purged = (
            await s.execute(
                delete(StorageObject)
                .where(
                    StorageObject.status == "deleted",
                    StorageObject.updated_at < now - DELETED_ROW_RETENTION,
                )
                .returning(StorageObject.id)
            )
        ).all()

    report = GcReport(len(abandoned), deleted, failed, len(purged))
    if any(vars(report).values()):
        log.info("storage_gc", **vars(report))
    return report
