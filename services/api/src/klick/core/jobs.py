"""Trabajos (ADR-006): la tabla `jobs` es la fuente de verdad; Dramatiq + Redis solo transportan."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

import structlog
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.models import Base, IdMixin
from klick.core.outbox import emit

if TYPE_CHECKING:
    from klick.core.worker import JobContext

log = structlog.get_logger("klick.jobs")

Queue = Literal["default", "media", "ai", "publish"]
QUEUES: tuple[Queue, ...] = ("default", "media", "ai", "publish")
ACTIVE_STATUSES = ("queued", "running", "retrying")
FINAL_STATUSES = ("succeeded", "failed", "canceled", "expired")


class Job(IdMixin, Base):
    __tablename__ = "jobs"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    queue: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    progress: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    locked_by: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="system")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------------ tipos de job

Handler = Callable[["JobContext"], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class JobKind:
    name: str
    queue: Queue
    handler: Handler
    # Permiso para crearlo/cancelarlo por la API; None = solo uso interno (no expuesto).
    required_permission: str | None = None
    public: bool = False
    max_attempts: int = 3
    timeout_s: float = 3600


JOB_KINDS: dict[str, JobKind] = {}


def job_kind(
    name: str,
    *,
    queue: Queue = "default",
    required_permission: str | None = None,
    public: bool = False,
    max_attempts: int = 3,
    timeout_s: float = 3600,
) -> Callable[[Handler], Handler]:
    """Registra un manejador de job. Los kinds ``public`` se pueden crear por la API."""

    def decorator(fn: Handler) -> Handler:
        if name in JOB_KINDS:
            raise ValueError(f"job kind duplicado: {name}")
        JOB_KINDS[name] = JobKind(
            name, queue, fn, required_permission, public, max_attempts, timeout_s
        )
        return fn

    return decorator


class PermanentJobError(Exception):
    """Fallo que no debe reintentarse."""


class JobCanceledError(Exception):
    """El job fue cancelado mientras se ejecutaba."""


# ----------------------------------------------------------------------------- despacho


@dataclass(frozen=True)
class JobRef:
    id: uuid.UUID
    org_id: uuid.UUID | None
    queue: str
    delay_s: float = 0


class Dispatcher(Protocol):
    async def dispatch(self, ref: JobRef) -> None: ...


class NullDispatcher:
    """Para pruebas y desarrollo sin broker: recuerda lo despachado."""

    def __init__(self) -> None:
        self.sent: list[JobRef] = []

    async def dispatch(self, ref: JobRef) -> None:
        self.sent.append(ref)


def backoff_s(attempt: int, base: float) -> float:
    """Espera antes del reintento ``attempt`` (1 = primer reintento): base·2^(n-1), máx. 15 min."""
    return float(min(base * 2 ** max(attempt - 1, 0), 900))


# ------------------------------------------------------------------------------ servicio


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Resource not found")


class JobService:
    def __init__(self, db: Database, dispatcher: Dispatcher) -> None:
        self._db = db
        self._dispatcher = dispatcher

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        kind: str,
        payload: dict[str, Any],
        org_id: uuid.UUID | None,
        created_by_type: str = "system",
        created_by: uuid.UUID | None = None,
        correlation_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        parent_job_id: uuid.UUID | None = None,
        delay_s: float = 0,
        priority: int = 0,
    ) -> tuple[Job, bool]:
        """Crea el job dentro de la transacción del llamador. Devuelve ``(job, creado)``.
        Con la misma ``idempotency_key`` (misma org y kind) devuelve el job existente."""
        spec = JOB_KINDS.get(kind)
        if spec is None:
            raise ApiError(
                422, "unknown_job_kind", "Unknown job kind", f"No existe el tipo {kind}."
            )
        if idempotency_key:
            existing = await self._by_key(session, org_id, kind, idempotency_key)
            if existing is not None:
                return existing, False
        now = datetime.now(UTC)
        job = Job(
            id=new_id(),
            org_id=org_id,
            queue=spec.queue,
            kind=kind,
            payload=payload,
            max_attempts=spec.max_attempts,
            run_after=now + timedelta(seconds=delay_s),
            deadline_at=None,
            correlation_id=correlation_id or new_id(),
            idempotency_key=idempotency_key,
            created_by_type=created_by_type,
            created_by=created_by,
            parent_job_id=parent_job_id,
            priority=priority,
        )
        try:
            async with session.begin_nested():
                session.add(job)
                await session.flush()
        except IntegrityError:
            # Carrera con otra petición que usó la misma clave de idempotencia.
            existing = await self._by_key(session, org_id, kind, idempotency_key or "")
            if existing is None:
                raise
            return existing, False
        await emit(
            session,
            type="job.created",
            org_id=org_id,
            aggregate_type="job",
            aggregate_id=job.id,
            correlation_id=job.correlation_id,
            payload={"job_id": str(job.id), "kind": kind, "queue": spec.queue},
        )
        return job, True

    async def _by_key(
        self, session: AsyncSession, org_id: uuid.UUID | None, kind: str, key: str
    ) -> Job | None:
        stmt = select(Job).where(Job.kind == kind, Job.idempotency_key == key)
        stmt = stmt.where(Job.org_id.is_(None) if org_id is None else Job.org_id == org_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def dispatch(self, job: Job) -> None:
        """Envía el mensaje al broker y marca `dispatched_at`. Si el broker falla el job queda
        en cola sin despachar y el reconciliador lo reenvía."""
        delay = max((job.run_after - datetime.now(UTC)).total_seconds(), 0)
        try:
            await self._dispatcher.dispatch(JobRef(job.id, job.org_id, job.queue, delay))
        except Exception:
            log.exception("job_dispatch_failed", job_id=str(job.id))
            return
        async with self._session(job.org_id) as s:
            await s.execute(
                update(Job).where(Job.id == job.id).values(dispatched_at=datetime.now(UTC))
            )

    def _session(self, org_id: uuid.UUID | None) -> Any:
        return self._db.tenant_session(org_id) if org_id else self._db.system_session()

    async def get(self, org_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        async with self._db.tenant_session(org_id) as s:
            job = (
                await s.execute(select(Job).where(Job.id == job_id, Job.org_id == org_id))
            ).scalar_one_or_none()
            if job is None:
                raise _not_found()
            return job

    async def list(
        self,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        cursor: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[Job], uuid.UUID | None]:
        limit = max(1, min(limit, 200))
        async with self._db.tenant_session(org_id) as s:
            stmt = select(Job).where(Job.org_id == org_id)
            if status:
                stmt = stmt.where(Job.status == status)
            if cursor:
                stmt = stmt.where(Job.id < cursor)  # UUIDv7: el orden del ID es el orden temporal
            rows = list((await s.execute(stmt.order_by(Job.id.desc()).limit(limit + 1))).scalars())
        next_cursor = rows[limit - 1].id if len(rows) > limit else None
        return rows[:limit], next_cursor

    async def cancel(self, org_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        """Cancela un job activo. Si está en ejecución, el manejador lo detecta en su siguiente
        punto de control (``ctx.raise_if_canceled()``)."""
        async with self._db.tenant_session(org_id) as s:
            job = (
                await s.execute(
                    select(Job).where(Job.id == job_id, Job.org_id == org_id).with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                raise _not_found()
            if job.status in FINAL_STATUSES:
                raise ApiError(
                    409, "job_finished", "Job already finished", "El trabajo ya terminó."
                )
            job.status = "canceled"
            job.finished_at = datetime.now(UTC)
            job.locked_by = None
            await emit(
                s,
                type="job.canceled",
                org_id=org_id,
                aggregate_type="job",
                aggregate_id=job.id,
                correlation_id=job.correlation_id,
                payload={"job_id": str(job.id), "kind": job.kind},
            )
            return job
