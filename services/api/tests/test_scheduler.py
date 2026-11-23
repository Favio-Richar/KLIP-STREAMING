import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from actors import Users, create_org
from harness import ApiHarness
from klick.core.db import Database
from klick.core.jobs import NullDispatcher
from klick.core.scheduler import (
    cleanup_idempotency,
    maintain_partitions,
    reconcile_jobs,
)
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


async def _job(api: ApiHarness, users: Users) -> tuple[uuid.UUID, uuid.UUID]:
    ana = await users.make("ana@example.com")
    org = (await create_org(ana))["id"]
    r = await ana.call("POST", "/jobs", org=org, json={"kind": "system.ping"})
    api.dispatcher.sent.clear()
    return uuid.UUID(r.json()["job_id"]), uuid.UUID(org)


async def _reconcile(api: ApiHarness) -> tuple[object, NullDispatcher]:
    dispatcher = NullDispatcher()
    report = await reconcile_jobs(api.app.state.db, dispatcher, api.settings)
    return report, dispatcher


async def _status(api: ApiHarness, job_id: uuid.UUID) -> asyncpg.Record:
    return (await _sql(api, "select * from jobs where id = $1", job_id))[0]


# ------------------------------------------------------------------------- reconciliador


async def test_lost_worker_is_requeued_and_redispatched(api: ApiHarness, users: Users) -> None:
    job_id, org = await _job(api, users)
    await _sql(
        api,
        "update jobs set status='running', attempts=1, locked_by='muerto', "
        "heartbeat_at = now() - interval '5 minutes' where id = $1",
        job_id,
    )
    report, dispatcher = await _reconcile(api)
    assert (report.requeued, report.redispatched) == (1, 1)  # type: ignore[attr-defined]
    job = await _status(api, job_id)
    assert job["status"] == "retrying" and job["locked_by"] is None
    assert "WorkerLost" in str(job["error"])
    assert [(r.id, r.org_id) for r in dispatcher.sent] == [(job_id, org)]
    assert job["dispatched_at"] is not None
    types = [r["type"] for r in await _sql(api, "select type from outbox_events")]
    assert "job.retrying" in types


async def test_lost_worker_with_no_attempts_left_fails(api: ApiHarness, users: Users) -> None:
    job_id, _ = await _job(api, users)
    await _sql(
        api,
        "update jobs set status='running', attempts=3, locked_by='muerto', "
        "heartbeat_at = now() - interval '5 minutes' where id = $1",
        job_id,
    )
    report, dispatcher = await _reconcile(api)
    assert report.failed == 1 and not dispatcher.sent  # type: ignore[attr-defined]
    job = await _status(api, job_id)
    assert job["status"] == "failed" and job["finished_at"] is not None


async def test_healthy_running_job_is_left_alone(api: ApiHarness, users: Users) -> None:
    job_id, _ = await _job(api, users)
    await _sql(
        api,
        "update jobs set status='running', attempts=1, locked_by='vivo', heartbeat_at = now() "
        "where id = $1",
        job_id,
    )
    report, dispatcher = await _reconcile(api)
    assert not any(vars(report).values()) and not dispatcher.sent
    assert (await _status(api, job_id))["status"] == "running"


async def test_overdue_jobs_expire(api: ApiHarness, users: Users) -> None:
    job_id, _ = await _job(api, users)
    await _sql(
        api, "update jobs set deadline_at = now() - interval '1 minute' where id = $1", job_id
    )
    report, _ = await _reconcile(api)
    assert report.expired == 1  # type: ignore[attr-defined]
    job = await _status(api, job_id)
    assert job["status"] == "expired" and job["finished_at"] is not None
    types = [r["type"] for r in await _sql(api, "select type from outbox_events")]
    assert "job.expired" in types
    again, _ = await _reconcile(api)
    assert not any(vars(again).values())  # idempotente


async def test_undispatched_jobs_are_sent_to_the_broker(api: ApiHarness, users: Users) -> None:
    job_id, org = await _job(api, users)
    await _sql(api, "update jobs set dispatched_at = null where id = $1", job_id)
    report, dispatcher = await _reconcile(api)
    assert report.redispatched == 1  # type: ignore[attr-defined]
    assert [(r.id, r.org_id, r.queue) for r in dispatcher.sent] == [(job_id, org, "default")]
    assert (await _status(api, job_id))["dispatched_at"] is not None
    again, dispatcher2 = await _reconcile(api)
    assert not dispatcher2.sent  # ya despachado: no se duplica


async def test_lost_messages_are_resent_after_the_grace_period(
    api: ApiHarness, users: Users
) -> None:
    job_id, _ = await _job(api, users)
    await _sql(
        api,
        "update jobs set dispatched_at = now() - interval '1 hour', run_after = now() - interval '1 hour' "
        "where id = $1",
        job_id,
    )
    report, dispatcher = await _reconcile(api)
    assert report.redispatched == 1 and len(dispatcher.sent) == 1  # type: ignore[attr-defined]


async def test_delayed_job_is_not_dispatched_early_but_keeps_its_delay(
    api: ApiHarness, users: Users
) -> None:
    ana = await users.make("ana@example.com")
    org = (await create_org(ana))["id"]
    r = await ana.call("POST", "/jobs", org=org, json={"kind": "system.ping", "delay_s": 600})
    job_id = uuid.UUID(r.json()["job_id"])
    assert api.dispatcher.sent[-1].delay_s > 590  # se envía con retraso
    await _sql(api, "update jobs set dispatched_at = null where id = $1", job_id)
    _, dispatcher = await _reconcile(api)
    assert dispatcher.sent and dispatcher.sent[0].delay_s > 590  # conserva el retraso


async def test_broker_failure_keeps_job_for_the_next_cycle(api: ApiHarness, users: Users) -> None:
    job_id, _ = await _job(api, users)
    await _sql(api, "update jobs set dispatched_at = null where id = $1", job_id)

    class Down:
        async def dispatch(self, ref: object) -> None:
            raise ConnectionError("broker caído")

    report = await reconcile_jobs(api.app.state.db, Down(), api.settings)
    assert report.redispatched == 0
    assert (await _status(api, job_id))["dispatched_at"] is None
    _, dispatcher = await _reconcile(api)
    assert len(dispatcher.sent) == 1


# ---------------------------------------------------------------------------- particiones


async def _partitions(api: ApiHarness) -> list[str]:
    rows = await _sql(
        api,
        "select c.relname from pg_inherits i join pg_class c on c.oid = i.inhrelid "
        "where i.inhparent = 'audit_events'::regclass order by 1",
    )
    return [r["relname"] for r in rows]


def _month(offset: int) -> str:
    now = datetime.now(UTC)
    total = now.year * 12 + now.month - 1 + offset
    return f"audit_events_{total // 12}_{total % 12 + 1:02d}"


async def test_future_partitions_exist_and_maintenance_is_idempotent(api: ApiHarness) -> None:
    names = await _partitions(api)
    for offset in range(4):  # mes en curso + 3 siguientes (docs/04 §4)
        assert _month(offset) in names, (offset, names)
    assert "audit_events_default" in names
    db: Database = api.app.state.db
    await maintain_partitions(db, api.settings)
    await maintain_partitions(db, api.settings)
    assert await _partitions(api) == names


async def test_expired_partitions_are_dropped_by_retention(api: ApiHarness) -> None:
    await _sql(
        api,
        "create table audit_events_2019_01 partition of audit_events "
        "for values from ('2019-01-01') to ('2019-02-01')",
    )
    await _sql(
        api,
        "insert into audit_events (id, actor_type, action, created_at) "
        "values (gen_random_uuid(), 'system', 'old', '2019-01-15')",
    )
    assert "audit_events_2019_01" in await _partitions(api)
    await maintain_partitions(api.app.state.db, api.settings)
    names = await _partitions(api)
    assert "audit_events_2019_01" not in names  # vencida (retención de 730 días)
    assert _month(0) in names and "audit_events_default" in names  # lo vigente se conserva


async def test_app_roles_cannot_manage_partitions_directly(api: ApiHarness) -> None:
    db = Database(api.settings.model_copy(update={"database_url": api.db_urls["app"]}))
    try:
        with pytest.raises(Exception, match="permission denied"):
            async with db.global_session() as s:
                from sqlalchemy import text  # noqa: PLC0415

                await s.execute(text("select ensure_monthly_partitions('audit_events', 1)"))
    finally:
        await db.dispose()


# ------------------------------------------------------------------------------ limpieza


async def test_expired_idempotency_keys_are_cleaned(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    org = (await create_org(ana))["id"]
    for key in ("a", "b"):
        await ana.call(
            "POST", "/jobs", org=org, json={"kind": "system.ping"}, headers={"Idempotency-Key": key}
        )
    await _sql(
        api, "update idempotency_keys set expires_at = now() - interval '1 hour' where key = 'a'"
    )
    assert await cleanup_idempotency(api.app.state.db) == 1
    left = await _sql(api, "select key from idempotency_keys")
    assert [r["key"] for r in left] == ["b"]
    assert timedelta(0) < timedelta(hours=api.settings.idempotency_ttl_h)


async def test_scheduler_loop_publishes_the_outbox_and_stops_cleanly(
    api: ApiHarness, users: Users
) -> None:
    import asyncio  # noqa: PLC0415

    from klick.core.outbox import emit  # noqa: PLC0415
    from klick.core.scheduler import run_scheduler  # noqa: PLC0415

    ana = await users.make("ana@example.com")
    org = uuid.UUID((await create_org(ana))["id"])
    db: Database = api.app.state.db
    async with db.tenant_session(org) as s:
        await emit(
            s, type="loop.test", org_id=org, aggregate_type="x", aggregate_id=None, payload={"n": 1}
        )
    stop = asyncio.Event()
    task = asyncio.create_task(run_scheduler(db, api.redis, NullDispatcher(), api.settings, stop))
    try:
        for _ in range(60):
            await asyncio.sleep(0.1)
            pending = await _sql(
                api, "select count(*) as n from outbox_events where published_at is null"
            )
            if pending[0]["n"] == 0:
                break
        assert pending[0]["n"] == 0, "el scheduler no publicó el outbox"
    finally:
        stop.set()
        await asyncio.wait_for(task, 10)
    assert task.done() and task.exception() is None
