"""Transporte Dramatiq sobre Redis. La verdad vive en la tabla `jobs`, no en el broker."""

import asyncio
import os
import socket
import threading
import uuid
from typing import Any

import dramatiq
import structlog
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from redis.asyncio import Redis

from klick.core.config import get_settings
from klick.core.db import Database
from klick.core.jobs import QUEUES, Dispatcher, JobRef
from klick.core.worker import WorkerDeps, execute_job

log = structlog.get_logger("klick.queue")

ACTOR_PREFIX = "klick_run_job_"
_TIME_LIMIT_MS = 6 * 3600 * 1000  # el límite real por tipo lo aplica execute_job


def setup_broker(redis_url: str) -> dramatiq.Broker:
    """Crea el broker (``stub://`` para pruebas) y declara un actor por cola."""
    broker: dramatiq.Broker = (
        StubBroker() if redis_url.startswith("stub://") else RedisBroker(url=redis_url)  # type: ignore[no-untyped-call]
    )
    dramatiq.set_broker(broker)
    for queue in QUEUES:
        dramatiq.actor(
            actor_name=f"{ACTOR_PREFIX}{queue}",
            queue_name=queue,
            broker=broker,
            max_retries=0,  # los reintentos los decide la tabla `jobs`, no Dramatiq
            time_limit=_TIME_LIMIT_MS,
        )(_run_job_sync)
    return broker


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def _run_job_sync(job_id: str, org_id: str) -> None:
    """Cuerpo del actor. Un bucle de eventos y un motor sin pool por mensaje (hilos de Dramatiq)."""
    asyncio.run(_run_job(uuid.UUID(job_id), uuid.UUID(org_id) if org_id else None))


async def _run_job(job_id: uuid.UUID, org_id: uuid.UUID | None) -> None:
    settings = get_settings()
    db = Database(settings, null_pool=True)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        deps = WorkerDeps(
            db=db,
            redis=redis,
            settings=settings,
            dispatcher=DramatiqDispatcher(dramatiq.get_broker()),
            worker_id=_worker_id(),
        )
        await execute_job(deps, job_id, org_id)
    finally:
        await redis.aclose()
        await db.dispose()


class DramatiqDispatcher(Dispatcher):
    def __init__(self, broker: dramatiq.Broker) -> None:
        self._broker = broker

    def _send(self, ref: JobRef) -> None:
        actor: Any = self._broker.get_actor(f"{ACTOR_PREFIX}{ref.queue}")
        options: dict[str, Any] = {}
        if ref.delay_s > 0:
            options["delay"] = int(ref.delay_s * 1000)
        actor.send_with_options(
            args=(str(ref.id), str(ref.org_id) if ref.org_id else ""), **options
        )

    async def dispatch(self, ref: JobRef) -> None:
        await asyncio.to_thread(self._send, ref)
