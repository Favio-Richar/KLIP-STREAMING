"""Ejecución de jobs: reclamo atómico, latido, progreso, reintentos con backoff y cierre (ADR-006)."""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from klick.core.config import Settings
from klick.core.db import Database
from klick.core.events import publish_ephemeral
from klick.core.jobs import (
    JOB_KINDS,
    Dispatcher,
    Job,
    JobCanceledError,
    JobRef,
    PermanentJobError,
    backoff_s,
)
from klick.core.outbox import emit

log = structlog.get_logger("klick.worker")

Outcome = str  # "succeeded" | "failed" | "retrying" | "canceled" | "skipped"


@dataclass
class WorkerDeps:
    db: Database
    redis: Redis
    settings: Settings
    dispatcher: Dispatcher
    worker_id: str


@dataclass(frozen=True)
class JobSnapshot:
    id: uuid.UUID
    org_id: uuid.UUID | None
    kind: str
    queue: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    correlation_id: uuid.UUID


class JobContext:
    """Lo que recibe un manejador: carga, progreso y puntos de control de cancelación."""

    def __init__(self, deps: WorkerDeps, job: JobSnapshot) -> None:
        self._deps = deps
        self.job = job
        self.job_id = job.id
        self.org_id = job.org_id
        self.kind = job.kind
        self.payload = job.payload
        self.attempt = job.attempts
        self.worker_id = deps.worker_id

    @property
    def dispatcher(self) -> Dispatcher:
        return self._deps.dispatcher

    @property
    def redis(self) -> Redis:
        return self._deps.redis

    @property
    def db(self) -> Database:
        return self._deps.db

    @property
    def settings(self) -> Settings:
        return self._deps.settings

    def _session(self) -> Any:
        return _session(self._deps.db, self.org_id)

    async def progress(self, percent: int, message: str | None = None) -> None:
        """Guarda el progreso (0-100), renueva el latido y lo emite por SSE (no durable)."""
        percent = max(0, min(100, int(percent)))
        async with self._session() as s:
            res = await s.execute(
                update(Job)
                .where(
                    Job.id == self.job_id, Job.status == "running", Job.locked_by == self.worker_id
                )
                .values(progress=percent, heartbeat_at=func.now())
                .returning(Job.id)
            )
            if res.first() is None:
                raise JobCanceledError()
        if self.org_id:
            await publish_ephemeral(
                self._deps.redis,
                org_id=self.org_id,
                type="job.progress",
                correlation_id=self.job.correlation_id,
                payload={
                    "job_id": str(self.job_id),
                    "kind": self.kind,
                    "progress": percent,
                    "message": message,
                },
            )

    async def is_canceled(self) -> bool:
        async with self._session() as s:
            status = (
                await s.execute(select(Job.status).where(Job.id == self.job_id))
            ).scalar_one_or_none()
        return bool(status != "running")

    async def raise_if_canceled(self) -> None:
        if await self.is_canceled():
            raise JobCanceledError()


def _session(db: Database, org_id: uuid.UUID | None) -> Any:
    return db.tenant_session(org_id) if org_id else db.system_session()


def _snapshot(job: Job) -> JobSnapshot:
    return JobSnapshot(
        id=job.id,
        org_id=job.org_id,
        kind=job.kind,
        queue=job.queue,
        payload=dict(job.payload),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        correlation_id=job.correlation_id,
    )


async def _claim(
    deps: WorkerDeps, job_id: uuid.UUID, org_id: uuid.UUID | None
) -> JobSnapshot | None:
    """Toma el job de forma atómica. Solo uno de varios mensajes duplicados lo consigue."""
    stmt = (
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_(("queued", "retrying")),
            Job.run_after <= func.now(),
            Job.org_id.is_(None) if org_id is None else Job.org_id == org_id,
        )
        .values(
            status="running",
            locked_by=deps.worker_id,
            started_at=func.coalesce(Job.started_at, func.now()),
            heartbeat_at=func.now(),
            attempts=Job.attempts + 1,
        )
        .returning(Job)
        .execution_options(synchronize_session=False)
    )
    async with _session(deps.db, org_id) as s:
        job = (await s.execute(stmt)).scalar_one_or_none()
        return _snapshot(job) if job else None


async def _heartbeat(deps: WorkerDeps, job: JobSnapshot) -> None:
    interval = deps.settings.job_heartbeat_interval_s
    while True:
        await asyncio.sleep(interval)
        try:
            async with _session(deps.db, job.org_id) as s:
                res = await s.execute(
                    update(Job)
                    .where(
                        Job.id == job.id, Job.status == "running", Job.locked_by == deps.worker_id
                    )
                    .values(heartbeat_at=func.now())
                    .returning(Job.id)
                )
                if res.first() is None:
                    return  # el job ya no es nuestro (cancelado o reasignado)
        except Exception:
            log.exception("heartbeat_failed", job_id=str(job.id))


def _error_info(exc: BaseException, attempt: int) -> dict[str, Any]:
    return {"type": type(exc).__name__, "message": str(exc)[:500], "attempt": attempt}


async def _finish(
    deps: WorkerDeps,
    job: JobSnapshot,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    event: str,
    run_after: datetime | None = None,
) -> bool:
    """Cierra el intento solo si el job sigue siendo nuestro. Emite el evento en la misma tx."""
    values: dict[str, Any] = {"status": status, "locked_by": None, "error": error}
    if status in ("succeeded", "failed", "canceled", "expired"):
        values["finished_at"] = func.now()
    if status == "succeeded":
        values.update(result=result, progress=100)
    if status == "retrying":
        values.update(run_after=run_after, dispatched_at=None)
    async with _session(deps.db, job.org_id) as s:
        res = await s.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "running", Job.locked_by == deps.worker_id)
            .values(**values)
            .returning(Job.id)
        )
        if res.first() is None:
            return False
        await _emit_state(s, job, event, status, error)
    return True


async def _emit_state(
    s: AsyncSession, job: JobSnapshot, event: str, status: str, error: dict[str, Any] | None
) -> None:
    await emit(
        s,
        type=event,
        org_id=job.org_id,
        aggregate_type="job",
        aggregate_id=job.id,
        correlation_id=job.correlation_id,
        payload={
            "job_id": str(job.id),
            "kind": job.kind,
            "status": status,
            "attempt": job.attempts,
            **({"error": error} if error else {}),
        },
    )


async def execute_job(deps: WorkerDeps, job_id: uuid.UUID, org_id: uuid.UUID | None) -> Outcome:
    """Ejecuta un job. Devuelve el resultado del intento. Nunca lanza por fallos del manejador."""
    job = await _claim(deps, job_id, org_id)
    if job is None:
        log.info("job_skipped", job_id=str(job_id))
        return "skipped"
    structlog.contextvars.bind_contextvars(
        job_id=str(job.id), org_id=str(job.org_id) if job.org_id else None
    )
    spec = JOB_KINDS.get(job.kind)
    heartbeat = asyncio.create_task(_heartbeat(deps, job))
    try:
        if spec is None:
            raise PermanentJobError(f"job kind desconocido: {job.kind}")
        result = await asyncio.wait_for(spec.handler(JobContext(deps, job)), spec.timeout_s)
    except JobCanceledError:
        await _finish(deps, job, status="canceled", event="job.canceled")
        log.info("job_canceled")
        return "canceled"
    except Exception as exc:
        return await _handle_failure(deps, job, exc)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
    done = await _finish(deps, job, status="succeeded", result=result or {}, event="job.succeeded")
    if not done:  # lo cancelaron o lo reasignaron mientras corría
        return "canceled"
    log.info("job_succeeded")
    return "succeeded"


async def _handle_failure(deps: WorkerDeps, job: JobSnapshot, exc: Exception) -> Outcome:
    error = _error_info(exc, job.attempts)
    retryable = not isinstance(exc, PermanentJobError)
    if retryable and job.attempts < job.max_attempts:
        delay = backoff_s(job.attempts, deps.settings.job_backoff_base_s)
        run_after = datetime.now(UTC) + timedelta(seconds=delay)
        ok = await _finish(
            deps, job, status="retrying", error=error, event="job.retrying", run_after=run_after
        )
        if ok:
            log.warning("job_retrying", delay_s=delay, error=error["type"])
            try:
                await deps.dispatcher.dispatch(JobRef(job.id, job.org_id, job.queue, delay))
                async with _session(deps.db, job.org_id) as s:
                    await s.execute(
                        update(Job).where(Job.id == job.id).values(dispatched_at=func.now())
                    )
            except Exception:
                log.exception("retry_dispatch_failed")  # el reconciliador lo reenviará
        return "retrying"
    await _finish(deps, job, status="failed", error=error, event="job.failed")
    log.error("job_failed", error=error["type"], message=error["message"])
    return "failed"
