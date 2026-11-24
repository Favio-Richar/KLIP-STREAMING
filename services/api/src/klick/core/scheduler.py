"""Scheduler: reconciliador de jobs, publicador del outbox, particiones y limpieza (ADR-006/007)."""

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select, text, update

from klick.core.config import Settings, get_settings
from klick.core.db import Database
from klick.core.idempotency import IdempotencyKey
from klick.core.jobs import Dispatcher, Job, JobRef
from klick.core.logging import configure_logging
from klick.core.outbox import emit, publish_pending
from klick.media.uploads import reap_expired_uploads
from klick.storage.backend import S3Backend, StorageBackend
from klick.storage.gc import collect_garbage

log = structlog.get_logger("klick.scheduler")

PARTITIONED_TABLES = ("audit_events",)
PARTITION_MONTHS_AHEAD = 3


@dataclass(frozen=True)
class ReconcileReport:
    expired: int = 0
    requeued: int = 0
    failed: int = 0
    redispatched: int = 0


async def reconcile_jobs(
    db: Database, dispatcher: Dispatcher, settings: Settings
) -> ReconcileReport:
    """Repara el estado de los jobs (ADR-006): vence los que pasaron su plazo, reencola los de
    workers perdidos (latido vencido) o los falla si agotaron intentos, y reenvía los que no
    llegaron al broker. Corre con el rol de sistema (ve todas las organizaciones)."""
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=settings.job_heartbeat_timeout_s)
    expired = requeued = failed = 0
    to_dispatch: list[JobRef] = []

    async with db.system_session() as s:
        # 1) plazo vencido
        for job in (
            await s.execute(
                select(Job)
                .where(Job.deadline_at < now, Job.status.in_(("queued", "retrying", "running")))
                .with_for_update(skip_locked=True)
            )
        ).scalars():
            job.status, job.finished_at, job.locked_by = "expired", now, None
            await _emit(s, job, "job.expired")
            expired += 1

        # 2) worker perdido: sin latido
        for job in (
            await s.execute(
                select(Job)
                .where(Job.status == "running", Job.heartbeat_at < stale_before)
                .with_for_update(skip_locked=True)
            )
        ).scalars():
            job.locked_by = None
            job.error = {
                "type": "WorkerLost",
                "message": "sin latido del worker",
                "attempt": job.attempts,
            }
            if job.attempts < job.max_attempts:
                job.status, job.run_after, job.dispatched_at = "retrying", now, None
                await _emit(s, job, "job.retrying")
                requeued += 1
            else:
                job.status, job.finished_at = "failed", now
                await _emit(s, job, "job.failed")
                failed += 1

        await s.flush()

        # 3) pendientes que nunca se despacharon, o cuyo mensaje pudo perderse
        redispatch_before = now - timedelta(seconds=settings.job_redispatch_after_s)
        for job in (
            await s.execute(
                select(Job)
                .where(
                    Job.status.in_(("queued", "retrying")),
                    or_(
                        Job.dispatched_at.is_(None),
                        (Job.dispatched_at < redispatch_before) & (Job.run_after <= now),
                    ),
                )
                .order_by(Job.priority.desc(), Job.created_at)
                .limit(500)
                .with_for_update(skip_locked=True)
            )
        ).scalars():
            delay = max((job.run_after - now).total_seconds(), 0)
            to_dispatch.append(JobRef(job.id, job.org_id, job.queue, delay))

    redispatched = 0
    for ref in to_dispatch:
        try:
            await dispatcher.dispatch(ref)
        except Exception:
            log.exception("redispatch_failed", job_id=str(ref.id))
            continue
        async with db.system_session() as s:
            await s.execute(update(Job).where(Job.id == ref.id).values(dispatched_at=func.now()))
        redispatched += 1

    report = ReconcileReport(expired, requeued, failed, redispatched)
    if any((expired, requeued, failed, redispatched)):
        log.info("jobs_reconciled", **report.__dict__)
    return report


async def _emit(s: Any, job: Job, event: str) -> None:
    await emit(
        s,
        type=event,
        org_id=job.org_id,
        aggregate_type="job",
        aggregate_id=job.id,
        correlation_id=job.correlation_id,
        payload={"job_id": str(job.id), "kind": job.kind, "status": job.status, "error": job.error},
    )


async def maintain_partitions(db: Database, settings: Settings) -> None:
    """Crea las particiones de los próximos meses y elimina las vencidas por retención."""
    async with db.system_session() as s:
        for table in PARTITIONED_TABLES:
            created = (
                await s.execute(
                    text("select ensure_monthly_partitions(:t, :n)"),
                    {"t": table, "n": PARTITION_MONTHS_AHEAD},
                )
            ).scalar_one()
            dropped = (
                await s.execute(
                    text("select drop_expired_partitions(:t, :d)"),
                    {"t": table, "d": settings.audit_retention_days},
                )
            ).scalar_one()
            if created or dropped:
                log.info("partitions_maintained", table=table, created=created, dropped=dropped)


async def cleanup_idempotency(db: Database) -> int:
    async with db.system_session() as s:
        result = await s.execute(
            delete(IdempotencyKey)
            .where(IdempotencyKey.expires_at < func.now())
            .returning(IdempotencyKey.key)
        )
        return len(result.all())


# ------------------------------------------------------------------------------- bucle


async def _every(
    name: str, interval_s: float, fn: Callable[[], Awaitable[object]], stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            await fn()
        except Exception:
            log.exception("scheduler_task_failed", task=name)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)


async def run_scheduler(
    db: Database,
    redis: Redis,
    dispatcher: Dispatcher,
    settings: Settings,
    stop: asyncio.Event,
    storage: StorageBackend | None = None,
) -> None:
    async def publish() -> None:
        while (
            await publish_pending(db, redis, settings.outbox_batch_size)
            >= settings.outbox_batch_size
        ):
            pass  # hay más pendientes: sigue sin esperar

    tasks = [
        asyncio.create_task(_every("outbox", 1, publish, stop)),
        asyncio.create_task(
            _every("reconcile", 15, lambda: reconcile_jobs(db, dispatcher, settings), stop)
        ),
        asyncio.create_task(
            _every("partitions", 6 * 3600, lambda: maintain_partitions(db, settings), stop)
        ),
        asyncio.create_task(_every("idempotency", 3600, lambda: cleanup_idempotency(db), stop)),
    ]
    if storage is not None:
        tasks.append(
            asyncio.create_task(
                _every("storage_gc", 3600, lambda: collect_garbage(db, storage, settings), stop)
            )
        )
        tasks.append(
            asyncio.create_task(
                _every("upload_reaper", 600, lambda: reap_expired_uploads(db, storage), stop)
            )
        )
    log.info("scheduler_started")
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("scheduler_stopped")


async def _main() -> None:
    import klick.jobs_handlers  # noqa: F401, PLC0415
    from klick.models import load_all_models  # noqa: PLC0415

    load_all_models()
    from klick.core.queue import DramatiqDispatcher, setup_broker  # noqa: PLC0415

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    db = Database(settings.model_copy(update={"database_url": settings.database_url_system}))
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    dispatcher = DramatiqDispatcher(setup_broker(settings.redis_url))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows no soporta add_signal_handler
            loop.add_signal_handler(sig, stop.set)
    try:
        await run_scheduler(db, redis, dispatcher, settings, stop, S3Backend(settings))
    finally:
        await redis.aclose()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
