"""Eventos de dominio versionados (ADR-007): sobre, Redis Streams y flujo SSE hacia el navegador."""

import json
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

GLOBAL_STREAM = "klick:events"
GLOBAL_MAXLEN = 100_000
ORG_MAXLEN = 1_000
SOURCE = "klick"
_STREAM_ID = re.compile(r"^\d+-\d+$")


def org_stream(org_id: uuid.UUID | str) -> str:
    return f"klick:org:{org_id}:events"


def envelope(
    *,
    event_id: uuid.UUID | str,
    type: str,  # noqa: A002
    org_id: uuid.UUID | str | None,
    correlation_id: uuid.UUID | str | None,
    occurred_at: datetime,
    payload: dict[str, Any],
    schema_version: int = 1,
) -> dict[str, Any]:
    """Sobre estándar del evento (ADR-007)."""
    return {
        "id": str(event_id),
        "type": type,
        "schema_version": schema_version,
        "source": SOURCE,
        "org_id": str(org_id) if org_id else None,
        "correlation_id": str(correlation_id) if correlation_id else None,
        "occurred_at": occurred_at.isoformat(),
        "payload": payload,
    }


async def publish_event(redis: Redis, event: dict[str, Any]) -> None:
    """Publica en el stream global y, si tiene organización, en el stream de la organización."""
    data = json.dumps(event, separators=(",", ":"), default=str)
    fields: dict[Any, Any] = {"type": event["type"], "data": data}
    pipe = redis.pipeline()
    pipe.xadd(GLOBAL_STREAM, fields, maxlen=GLOBAL_MAXLEN, approximate=True)
    if event.get("org_id"):
        pipe.xadd(org_stream(event["org_id"]), fields, maxlen=ORG_MAXLEN, approximate=True)
    await pipe.execute()


async def publish_ephemeral(
    redis: Redis,
    *,
    org_id: uuid.UUID | str,
    type: str,  # noqa: A002
    payload: dict[str, Any],
    correlation_id: uuid.UUID | str | None = None,
) -> None:
    """Evento no durable (p. ej. progreso): solo va al stream de la organización para el SSE."""
    event = envelope(
        event_id=uuid.uuid4(),
        type=type,
        org_id=org_id,
        correlation_id=correlation_id,
        occurred_at=datetime.now(UTC),
        payload=payload,
    )
    data = json.dumps(event, separators=(",", ":"), default=str)
    fields: dict[Any, Any] = {"type": type, "data": data}
    await redis.xadd(org_stream(org_id), fields, maxlen=ORG_MAXLEN, approximate=True)


def parse_last_event_id(value: str | None) -> str:
    """``$`` (solo nuevos) salvo que el cliente reanude con un ID de stream válido."""
    return value if value and _STREAM_ID.match(value) else "$"


def _frame(event_id: str, event_type: str, data: str) -> str:
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


async def sse_stream(
    redis: Redis,
    org_id: uuid.UUID,
    *,
    last_event_id: str | None = None,
    keepalive_s: float = 15,
    max_idle_cycles: int | None = None,
) -> AsyncGenerator[str, None]:
    """Genera tramas SSE de los eventos de UNA organización. Termina si el cliente se desconecta
    (el generador se cierra) o tras ``max_idle_cycles`` ciclos sin eventos (solo para pruebas)."""
    cursor = parse_last_event_id(last_event_id)
    key = org_stream(org_id)
    if cursor == "$":
        # Se fija ya el último ID existente: lo publicado después de conectar no se pierde
        # aunque ocurra antes de la primera lectura bloqueante.
        last: Any = await redis.xrevrange(key, count=1)
        cursor = last[0][0] if last else "0-0"
    yield "retry: 3000\n\n"
    idle = 0
    while True:
        result: Any = await redis.xread({key: cursor}, block=int(keepalive_s * 1000), count=100)
        if not result:
            idle += 1
            if max_idle_cycles is not None and idle >= max_idle_cycles:
                return
            yield ": keepalive\n\n"
            continue
        idle = 0
        for _stream, entries in result:
            for stream_id, fields in entries:
                cursor = stream_id
                yield _frame(stream_id, fields["type"], fields["data"])
