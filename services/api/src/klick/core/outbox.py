"""Transactional outbox (ADR-007, C8): el evento se escribe en la misma transacción del cambio."""

import uuid
from datetime import datetime
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import DateTime, Integer, Text, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from klick.core.db import Database
from klick.core.events import envelope, publish_event
from klick.core.ids import new_id
from klick.core.models import Base

log = structlog.get_logger("klick.outbox")


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


async def emit(
    session: AsyncSession,
    *,
    type: str,  # noqa: A002
    org_id: uuid.UUID | None,
    aggregate_type: str,
    aggregate_id: uuid.UUID | None,
    payload: dict[str, Any],
    correlation_id: uuid.UUID | None = None,
    schema_version: int = 1,
) -> OutboxEvent:
    """Registra un evento de dominio dentro de la transacción de ``session``."""
    event = OutboxEvent(
        id=new_id(),
        org_id=org_id,
        type=type,
        schema_version=schema_version,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        correlation_id=correlation_id,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    return event


async def publish_pending(db: Database, redis: Redis, batch_size: int = 100) -> int:
    """Publica en Redis Streams los eventos pendientes y los marca. Al menos una vez:
    si el proceso cae entre XADD y el commit, el evento se republica (consumidores idempotentes).
    Devuelve cuántos publicó. Usa el rol de sistema: ve eventos de todas las organizaciones."""
    published = 0
    async with db.system_session() as s:
        rows = (
            await s.execute(
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
        events = list(rows)
        for ev in events:
            await publish_event(
                redis,
                envelope(
                    event_id=ev.id,
                    type=ev.type,
                    org_id=ev.org_id,
                    correlation_id=ev.correlation_id,
                    occurred_at=ev.created_at,
                    payload=ev.payload,
                    schema_version=ev.schema_version,
                ),
            )
            published += 1
        if events:
            await s.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id.in_([e.id for e in events]))
                .values(published_at=func.now())
            )
    if published:
        log.info("outbox_published", count=published)
    return published
