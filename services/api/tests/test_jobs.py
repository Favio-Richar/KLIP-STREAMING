import asyncio
import uuid
from typing import Any

import asyncpg
import pytest
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from klick.core.db import Database
from klick.core.events import org_stream
from klick.core.jobs import JOB_KINDS, JobService, NullDispatcher, backoff_s
from klick.core.worker import WorkerDeps, execute_job
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


def _deps(api: ApiHarness, worker_id: str = "w1", **overrides: Any) -> WorkerDeps:
    db: Database = api.app.state.db
    return WorkerDeps(
        db=db,
        redis=api.redis,
        settings=api.settings.model_copy(update={"job_backoff_base_s": 0.01, **overrides}),
        dispatcher=api.dispatcher,
        worker_id=worker_id,
    )


async def _owner(api: ApiHarness, users: Users) -> tuple[Actor, str]:
    ana = await users.make("ana@example.com")
    org = await create_org(ana)
    return ana, org["id"]


async def _ping(
    ana: Actor, org: str, payload: dict[str, Any] | None = None, **kw: Any
) -> dict[str, Any]:
    r = await ana.call(
        "POST",
        "/jobs",
        org=org,
        json={"kind": "system.ping", "payload": payload or {"steps": 3}, **kw},
    )
    assert r.status_code == 202, r.text
    data: dict[str, Any] = r.json()
    return data


async def _job(api: ApiHarness, job_id: str) -> asyncpg.Record:
    return (await _sql(api, "select * from jobs where id = $1", uuid.UUID(job_id)))[0]


# ----------------------------------------------------------------------------------- API


async def test_create_job_queues_and_dispatches(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    data = await _ping(ana, org)
    assert data["status_url"] == f"/api/v1/jobs/{data['job_id']}"
    job = await _job(api, data["job_id"])
    assert job["status"] == "queued" and job["queue"] == "default" and job["max_attempts"] == 3
    assert job["dispatched_at"] is not None
    assert [r.id for r in api.dispatcher.sent] == [uuid.UUID(data["job_id"])]
    assert str(api.dispatcher.sent[0].org_id) == org
    events = await _sql(api, "select type from outbox_events where type = 'job.created'")
    assert len(events) == 1
    audit = await _sql(
        api, "select action, actor_type from audit_events where action = 'job.created'"
    )
    assert [(r["action"], r["actor_type"]) for r in audit] == [("job.created", "user")]


async def test_job_creation_rules(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    vic = await users.make("vic@example.com")
    await add_member(api, ana, org, vic, "viewer")
    denied = await vic.call("POST", "/jobs", org=org, json={"kind": "system.ping"})
    assert denied.status_code == 403
    unknown = await ana.call("POST", "/jobs", org=org, json={"kind": "no.existe"})
    assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_job_kind"
    extra = await ana.call("POST", "/jobs", org=org, json={"kind": "system.ping", "owner": "x"})
    assert extra.status_code == 422
    assert not api.dispatcher.sent
    for kind in JOB_KINDS.values():  # la política de seguridad asume este permiso (sec_policy)
        if kind.public:
            assert kind.required_permission == "org:manage"


async def test_idempotency_key_replays_the_same_job(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    body = {"kind": "system.ping", "payload": {"steps": 2}}
    h = {"Idempotency-Key": "key-1"}
    first = await ana.call("POST", "/jobs", org=org, json=body, headers=h)
    second = await ana.call("POST", "/jobs", org=org, json=body, headers=h)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    assert (await _sql(api, "select count(*) as n from jobs"))[0]["n"] == 1
    assert len(api.dispatcher.sent) == 1
    clash = await ana.call(
        "POST", "/jobs", org=org, json={**body, "payload": {"steps": 9}}, headers=h
    )
    assert clash.status_code == 409 and clash.json()["code"] == "idempotency_key_reuse"
    bad = await ana.call(
        "POST", "/jobs", org=org, json=body, headers={"Idempotency-Key": "k" * 300}
    )
    assert bad.status_code == 400
    other = await ana.call(
        "POST", "/jobs", org=org, json=body, headers={"Idempotency-Key": "key-2"}
    )
    assert other.json()["job_id"] != first.json()["job_id"]


async def test_job_level_idempotency_is_unique_per_org_and_kind(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _owner(api, users)
    jobs: JobService = api.app.state.job_service
    db: Database = api.app.state.db
    async with db.tenant_session(uuid.UUID(org)) as s:
        a, created_a = await jobs.enqueue(
            s, kind="system.ping", payload={}, org_id=uuid.UUID(org), idempotency_key="k"
        )
        b, created_b = await jobs.enqueue(
            s, kind="system.ping", payload={}, org_id=uuid.UUID(org), idempotency_key="k"
        )
    assert created_a and not created_b and a.id == b.id


async def test_list_get_and_cancel_jobs(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    ids = [(await _ping(ana, org))["job_id"] for _ in range(3)]
    page1 = (await ana.call("GET", "/jobs?limit=2", org=org)).json()
    assert [j["id"] for j in page1["items"]] == [ids[2], ids[1]]  # más recientes primero
    page2 = (await ana.call("GET", f"/jobs?limit=2&cursor={page1['next_cursor']}", org=org)).json()
    assert [j["id"] for j in page2["items"]] == [ids[0]] and page2["next_cursor"] is None
    queued = (await ana.call("GET", "/jobs?status=queued", org=org)).json()["items"]
    assert len(queued) == 3
    one = (await ana.call("GET", f"/jobs/{ids[0]}", org=org)).json()
    assert one["kind"] == "system.ping" and one["progress"] == 0
    assert (await ana.call("GET", f"/jobs/{uuid.uuid4()}", org=org)).status_code == 404

    canceled = await ana.call("POST", f"/jobs/{ids[0]}/cancel", org=org)
    assert canceled.status_code == 200 and canceled.json()["status"] == "canceled"
    again = await ana.call("POST", f"/jobs/{ids[0]}/cancel", org=org)
    assert again.status_code == 409 and again.json()["code"] == "job_finished"
    types = {r["type"] for r in await _sql(api, "select type from outbox_events")}
    assert {"job.created", "job.canceled"} <= types


async def test_jobs_are_isolated_between_organizations(api: ApiHarness, users: Users) -> None:
    ana, org_a = await _owner(api, users)
    bruno = await users.make("bruno@example.com")
    org_b = (await create_org(bruno, "Org B"))["id"]
    job = (await _ping(ana, org_a))["job_id"]
    assert (await bruno.call("GET", f"/jobs/{job}", org=org_b)).status_code == 404
    assert (await bruno.call("POST", f"/jobs/{job}/cancel", org=org_b)).status_code == 404
    assert (await bruno.call("GET", "/jobs", org=org_b)).json()["items"] == []
    assert (await ana.call("GET", f"/jobs/{job}", org=org_a)).status_code == 200


# ------------------------------------------------------------------------------ ejecución


async def test_execute_job_success_reports_progress_and_events(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _owner(api, users)
    job_id = (await _ping(ana, org, {"steps": 4}))["job_id"]
    outcome = await execute_job(_deps(api), uuid.UUID(job_id), uuid.UUID(org))
    assert outcome == "succeeded"
    job = await _job(api, job_id)
    assert job["status"] == "succeeded" and job["progress"] == 100 and job["attempts"] == 1
    assert job["finished_at"] is not None and job["locked_by"] is None
    result = (await ana.call("GET", f"/jobs/{job_id}", org=org)).json()["result"]
    assert result["pong"] is True and result["steps"] == 4 and result["worker"] == "w1"
    types = [
        r["type"] for r in await _sql(api, "select type from outbox_events order by created_at")
    ]
    assert "job.succeeded" in types

    entries: Any = await api.redis.xrange(org_stream(org))
    progress = [e[1] for e in entries if e[1]["type"] == "job.progress"]
    assert len(progress) == 4
    assert '"progress":100' in progress[-1]["data"]
    assert '"message":"paso 4 de 4"' in progress[-1]["data"]


async def test_second_claim_of_the_same_job_is_skipped(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    job_id = uuid.UUID((await _ping(ana, org, {"steps": 2, "delay_ms": 150}))["job_id"])
    results = await asyncio.gather(
        execute_job(_deps(api, "w1"), job_id, uuid.UUID(org)),
        execute_job(_deps(api, "w2"), job_id, uuid.UUID(org)),
    )
    assert sorted(results) == ["skipped", "succeeded"]
    assert (await _job(api, str(job_id)))["attempts"] == 1
    # y un mensaje duplicado tardío también se ignora
    assert await execute_job(_deps(api, "w3"), job_id, uuid.UUID(org)) == "skipped"


async def test_transient_failure_retries_with_backoff_then_succeeds(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _owner(api, users)
    job_id = uuid.UUID((await _ping(ana, org, {"steps": 1, "fail_until_attempt": 1}))["job_id"])
    org_id = uuid.UUID(org)
    first = await execute_job(_deps(api), job_id, org_id)
    assert first == "retrying"
    job = await _job(api, str(job_id))
    assert job["status"] == "retrying" and job["attempts"] == 1
    assert job["error"] is not None and "fallo transitorio" in str(job["error"])
    assert job["locked_by"] is None
    retry_ref = api.dispatcher.sent[-1]
    assert retry_ref.id == job_id and retry_ref.delay_s > 0  # se reenvió con retraso
    await asyncio.sleep(0.05)
    assert await execute_job(_deps(api), job_id, org_id) == "succeeded"
    job = await _job(api, str(job_id))
    assert job["status"] == "succeeded" and job["attempts"] == 2 and job["error"] is None


async def test_retry_is_not_claimed_before_its_time(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    job_id = uuid.UUID((await _ping(ana, org, {"steps": 1, "fail_until_attempt": 1}))["job_id"])
    org_id = uuid.UUID(org)
    deps = _deps(api, job_backoff_base_s=60)
    assert await execute_job(deps, job_id, org_id) == "retrying"
    assert await execute_job(deps, job_id, org_id) == "skipped"  # run_after en el futuro


async def test_permanent_failure_does_not_retry(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    job_id = (await _ping(ana, org, {"steps": 1, "fail_permanently": True}))["job_id"]
    assert await execute_job(_deps(api), uuid.UUID(job_id), uuid.UUID(org)) == "failed"
    job = await _job(api, job_id)
    assert job["status"] == "failed" and job["attempts"] == 1
    types = [r["type"] for r in await _sql(api, "select type from outbox_events")]
    assert "job.failed" in types


async def test_failure_after_max_attempts(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    job_id = uuid.UUID((await _ping(ana, org, {"steps": 1, "fail_until_attempt": 99}))["job_id"])
    outcomes = []
    for _ in range(3):
        outcomes.append(await execute_job(_deps(api), job_id, uuid.UUID(org)))
        await asyncio.sleep(0.1)
    assert outcomes == ["retrying", "retrying", "failed"]
    job = await _job(api, str(job_id))
    assert job["status"] == "failed" and job["attempts"] == 3 and job["finished_at"] is not None


async def test_cancel_while_running_stops_the_handler(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    job_id = (await _ping(ana, org, {"steps": 20, "delay_ms": 100}))["job_id"]
    runner = asyncio.create_task(execute_job(_deps(api), uuid.UUID(job_id), uuid.UUID(org)))
    for _ in range(50):
        await asyncio.sleep(0.05)
        if (await _job(api, job_id))["status"] == "running":
            break
    assert (await ana.call("POST", f"/jobs/{job_id}/cancel", org=org)).status_code == 200
    assert await asyncio.wait_for(runner, 10) == "canceled"
    job = await _job(api, job_id)
    assert job["status"] == "canceled" and job["progress"] < 100


async def test_heartbeat_is_renewed_while_running(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    job_id = (await _ping(ana, org, {"steps": 1, "delay_ms": 600}))["job_id"]
    deps = _deps(api, job_heartbeat_interval_s=0.1)
    runner = asyncio.create_task(execute_job(deps, uuid.UUID(job_id), uuid.UUID(org)))
    await asyncio.sleep(0.3)
    first = (await _job(api, job_id))["heartbeat_at"]
    await asyncio.sleep(0.3)
    second = (await _job(api, job_id))["heartbeat_at"]
    assert first is not None and second > first
    await runner


async def test_unknown_kind_in_database_fails_permanently(api: ApiHarness, users: Users) -> None:
    ana, org = await _owner(api, users)
    job_id = (await _ping(ana, org))["job_id"]
    await _sql(api, "update jobs set kind = 'ya.no.existe' where id = $1", uuid.UUID(job_id))
    assert await execute_job(_deps(api), uuid.UUID(job_id), uuid.UUID(org)) == "failed"


def test_backoff_grows_exponentially_and_is_capped() -> None:
    assert [backoff_s(n, 5) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert backoff_s(20, 5) == 900


async def test_null_dispatcher_records_refs() -> None:
    d = NullDispatcher()
    await d.dispatch(
        __import__("klick.core.jobs", fromlist=["JobRef"]).JobRef(uuid.uuid4(), None, "default")
    )
    assert len(d.sent) == 1
