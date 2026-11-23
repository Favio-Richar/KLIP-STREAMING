"""Idempotencia de peticiones (docs/05 §1): misma clave + mismo cuerpo = misma respuesta;
misma clave + cuerpo distinto = 409. No se usa en operaciones cuya respuesta contiene secretos
(p. ej. crear un token de API): la respuesta se guarda en claro en la base."""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import DateTime, SmallInteger, Text, delete, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from klick.core.config import Settings
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.models import Base

HEADER = "Idempotency-Key"
MAX_KEY_LEN = 255
STALE_IN_PROGRESS = timedelta(minutes=5)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def request_hash(method: str, scope: str, body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{method}\n{scope}\n{canonical}".encode()).hexdigest()


def validate_key(raw: str) -> str:
    if not 1 <= len(raw) <= MAX_KEY_LEN or not raw.isascii() or not raw.isprintable():
        raise ApiError(
            400,
            "invalid_idempotency_key",
            "Invalid idempotency key",
            f"{HEADER} debe tener entre 1 y {MAX_KEY_LEN} caracteres ASCII imprimibles.",
        )
    return raw


class IdempotencyStore:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._ttl = timedelta(hours=settings.idempotency_ttl_h)

    async def run(
        self,
        request: Request,
        org_id: uuid.UUID,
        scope: str,
        body: Any,
        producer: Callable[[], Awaitable[tuple[int, Any]]],
    ) -> JSONResponse:
        """Ejecuta ``producer`` (devuelve ``(status, cuerpo JSON)``) una sola vez por clave."""
        raw = request.headers.get(HEADER)
        if raw is None:
            status, payload = await producer()
            return JSONResponse(payload, status_code=status)
        key = validate_key(raw)
        digest = request_hash(request.method, scope, body)

        replay = await self._begin(org_id, scope, key, digest)
        if replay is not None:
            return JSONResponse(
                replay[1], status_code=replay[0], headers={"Idempotent-Replayed": "true"}
            )
        try:
            status, payload = await producer()
        except BaseException:
            await self._abort(org_id, scope, key)  # los fallos no se cachean: se puede reintentar
            raise
        await self._complete(org_id, scope, key, status, payload)
        return JSONResponse(payload, status_code=status)

    async def _begin(
        self, org_id: uuid.UUID, scope: str, key: str, digest: str
    ) -> tuple[int, Any] | None:
        now = datetime.now(UTC)
        async with self._db.tenant_session(org_id) as s:
            await s.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.org_id == org_id,
                    IdempotencyKey.scope == scope,
                    IdempotencyKey.key == key,
                    IdempotencyKey.expires_at < now,
                )
            )
            inserted = await s.execute(
                text(
                    "insert into idempotency_keys (org_id, scope, key, request_hash, status, expires_at) "
                    "values (:o, :s, :k, :h, 'in_progress', :e) on conflict do nothing returning key"
                ),
                {"o": org_id, "s": scope, "k": key, "h": digest, "e": now + self._ttl},
            )
            if inserted.first() is not None:
                return None
            row = (
                await s.execute(
                    select(IdempotencyKey)
                    .where(
                        IdempotencyKey.org_id == org_id,
                        IdempotencyKey.scope == scope,
                        IdempotencyKey.key == key,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if row.request_hash != digest:
                raise ApiError(
                    409,
                    "idempotency_key_reuse",
                    "Idempotency key reused",
                    "Esa clave ya se usó con una petición distinta.",
                )
            if row.status == "completed":
                return int(row.response_status or 200), row.response
            if now - row.created_at > STALE_IN_PROGRESS:
                # El intento anterior murió sin cerrar: se retoma.
                await s.execute(
                    update(IdempotencyKey)
                    .where(
                        IdempotencyKey.org_id == org_id,
                        IdempotencyKey.scope == scope,
                        IdempotencyKey.key == key,
                    )
                    .values(created_at=now)
                )
                return None
            raise ApiError(
                409,
                "idempotency_in_progress",
                "Request in progress",
                "La petición original sigue en curso.",
                headers={"Retry-After": "1"},
            )

    async def _complete(
        self, org_id: uuid.UUID, scope: str, key: str, status: int, payload: Any
    ) -> None:
        async with self._db.tenant_session(org_id) as s:
            await s.execute(
                update(IdempotencyKey)
                .where(
                    IdempotencyKey.org_id == org_id,
                    IdempotencyKey.scope == scope,
                    IdempotencyKey.key == key,
                )
                .values(status="completed", response_status=status, response=payload)
            )

    async def _abort(self, org_id: uuid.UUID, scope: str, key: str) -> None:
        async with self._db.tenant_session(org_id) as s:
            await s.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.org_id == org_id,
                    IdempotencyKey.scope == scope,
                    IdempotencyKey.key == key,
                    IdempotencyKey.status == "in_progress",
                )
            )


def get_idempotency(request: Request) -> IdempotencyStore:
    store: IdempotencyStore = request.app.state.idempotency
    return store


Idem = Annotated[IdempotencyStore, Depends(get_idempotency)]
