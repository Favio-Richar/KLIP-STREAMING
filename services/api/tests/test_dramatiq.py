"""Integración con Dramatiq: broker en memoria (StubBroker) y un Worker real ejecutando el actor."""

import asyncio
import uuid
from collections.abc import Iterator

import asyncpg
import dramatiq
import dramatiq.broker
import pytest
from actors import Users, create_org
from dramatiq import Worker
from harness import ApiHarness
from klick.core.config import get_settings
from klick.core.jobs import JobRef
from klick.core.queue import ACTOR_PREFIX, DramatiqDispatcher, setup_broker
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _status(api: ApiHarness, job_id: uuid.UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return (await conn.fetch("select * from jobs where id = $1", job_id))[0]
    finally:
        await conn.close()


@pytest.fixture
def stub_broker(api: ApiHarness, monkeypatch: pytest.MonkeyPatch) -> Iterator[dramatiq.Broker]:
    """El actor lee la configuración de entorno: se apunta a la base y Redis de la prueba."""
    monkeypatch.setenv("DATABASE_URL", api.db_urls["app"])
    monkeypatch.setenv("DATABASE_URL_SYSTEM", api.db_urls["system"])
    monkeypatch.setenv("REDIS_URL", api.settings.redis_url)
    get_settings.cache_clear()
    previous = dramatiq.broker.global_broker
    broker = setup_broker("stub://")
    yield broker
    broker.close()
    get_settings.cache_clear()
    if previous is not None:
        dramatiq.set_broker(previous)


def test_one_actor_per_queue_without_dramatiq_retries(stub_broker: dramatiq.Broker) -> None:
    for queue in ("default", "media", "ai", "publish"):
        actor = stub_broker.get_actor(f"{ACTOR_PREFIX}{queue}")
        assert actor.queue_name == queue
        assert actor.options["max_retries"] == 0  # los reintentos los controla la tabla jobs


async def test_dispatch_enqueues_a_message_with_ids_and_delay(
    stub_broker: dramatiq.Broker,
) -> None:
    job, org = uuid.uuid4(), uuid.uuid4()
    dispatcher = DramatiqDispatcher(stub_broker)
    await dispatcher.dispatch(JobRef(job, org, "media"))
    await dispatcher.dispatch(JobRef(job, None, "default", delay_s=30))
    media = stub_broker.queues["media"]
    assert media.qsize() == 1
    message = dramatiq.Message.decode(media.get_nowait())
    assert message.args == (str(job), str(org))
    delayed = dramatiq.Message.decode(
        stub_broker.queues["default.DQ"].get_nowait()
    )  # los diferidos van a la cola DQ
    assert delayed.args == (str(job), "") and delayed.options["eta"] > 0


async def test_real_dramatiq_worker_executes_the_job_end_to_end(
    api: ApiHarness, users: Users, stub_broker: dramatiq.Broker
) -> None:
    ana = await users.make("ana@example.com")
    org = (await create_org(ana))["id"]
    created = await ana.call(
        "POST", "/jobs", org=org, json={"kind": "system.ping", "payload": {"steps": 3}}
    )
    job_id = uuid.UUID(created.json()["job_id"])

    await DramatiqDispatcher(stub_broker).dispatch(JobRef(job_id, uuid.UUID(org), "default"))
    worker = Worker(stub_broker, worker_timeout=100)
    worker.start()
    try:
        await asyncio.to_thread(stub_broker.join, "default", timeout=30000)
        await asyncio.to_thread(worker.join)
    finally:
        worker.stop()

    job = await _status(api, job_id)
    assert job["status"] == "succeeded" and job["progress"] == 100 and job["attempts"] == 1
    assert job["locked_by"] is None
    # el worker real registró su identidad (host:pid:hilo), no la de la prueba
    assert job["result"] is not None and '"worker"' in job["result"]


async def test_duplicate_messages_do_not_run_the_job_twice(
    api: ApiHarness, users: Users, stub_broker: dramatiq.Broker
) -> None:
    ana = await users.make("ana@example.com")
    org = uuid.UUID((await create_org(ana))["id"])
    job_id = uuid.UUID(
        (await ana.call("POST", "/jobs", org=str(org), json={"kind": "system.ping"})).json()[
            "job_id"
        ]
    )
    dispatcher = DramatiqDispatcher(stub_broker)
    for _ in range(3):  # el broker entrega "al menos una vez"
        await dispatcher.dispatch(JobRef(job_id, org, "default"))
    worker = Worker(stub_broker, worker_timeout=100)
    worker.start()
    try:
        await asyncio.to_thread(stub_broker.join, "default", timeout=30000)
        await asyncio.to_thread(worker.join)
    finally:
        worker.stop()
    assert (await _status(api, job_id))["attempts"] == 1
