"""Auditoría append-only (docs/04 §3.14, docs/09). Se escribe en la misma transacción del cambio."""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import DateTime, Text, func, insert
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from klick.core.ids import new_id
from klick.core.models import Base

ActorType = Literal["user", "api_token", "mcp", "agent", "system"]


@dataclass(frozen=True)
class RequestInfo:
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None


# El middleware lo fija por petición; los servicios lo leen sin recibir la petición HTTP.
request_info: ContextVar[RequestInfo | None] = ContextVar("request_info", default=None)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# Campos que nunca deben entrar en `diff` aunque alguien los pase por error.
_FORBIDDEN = ("password", "secret", "token", "authorization", "api_key", "cookie", "recovery")


def _scrub(diff: dict[str, Any] | None) -> dict[str, Any] | None:
    if diff is None:
        return None
    return {
        k: "[redacted]" if any(m in k.lower() for m in _FORBIDDEN) else v for k, v in diff.items()
    }


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_type: ActorType,
    actor_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    diff: dict[str, Any] | None = None,
) -> None:
    """Inserta un evento de auditoría dentro de la transacción de ``session``."""
    info = request_info.get() or RequestInfo()
    await session.execute(
        insert(AuditEvent).values(
            id=new_id(),
            # Explícito para evitar `RETURNING created_at`: con org_id nulo exigiría pasar la
            # política de SELECT (los eventos sin organización no son legibles por la app).
            created_at=datetime.now(UTC),
            org_id=org_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip=info.ip,
            user_agent=(info.user_agent or "")[:300] or None,
            request_id=info.request_id,
            diff=_scrub(diff),
        )
    )
