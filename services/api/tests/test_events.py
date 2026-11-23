import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import uvicorn
from actors import BASE, Actor, Users, create_org
from fastapi import FastAPI
from harness import ApiHarness
from klick.core.db import Database
from klick.core.events import (
    GLOBAL_STREAM,
    envelope,
    org_stream,
    parse_last_event_id,
    publish_event,
    sse_stream,
)
from klick.core.outbox import emit, publish_pending
from klick.core.worker import WorkerDeps, execute_job

pytestmark = pytest.mark.db


async def _emit(
    api: ApiHarness, org: uuid.UUID, type_: str = "thing.happened", **payload: Any
) -> None:
    db: Database = api.app.state.db
    async with db.tenant_session(org) as s:
        await emit(
            s,
            type=type_,
            org_id=org,
            aggregate_type="thing",
            aggregate_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            payload=payload,
        )


async def _entries(redis: Any, key: str) -> list[tuple[str, dict[str, str]]]:
    result: Any = await redis.xrange(key)
    return list(result)


async def _publish(api: ApiHarness) -> int:
    return await publish_pending(api.app.state.db, api.redis, 100)


async def _two_orgs(api: ApiHarness, users: Users) -> tuple[Actor, uuid.UUID, Actor, uuid.UUID]:
    ana, bruno = await users.make("ana@example.com"), await users.make("bruno@example.com")
    a = uuid.UUID((await create_org(ana, "Org A"))["id"])
    b = uuid.UUID((await create_org(bruno, "Org B"))["id"])
    await _publish(api)  # descarta lo que generó la creación de organizaciones
    await api.redis.flushdb()
    return ana, a, bruno, b


# ------------------------------------------------------------------------------ outbox


async def test_outbox_publishes_envelope_to_global_and_org_streams(
    api: ApiHarness, users: Users
) -> None:
    _, org_a, _, org_b = await _two_orgs(api, users)
    await _emit(api, org_a, n=1)
    await _emit(api, org_b, n=2)
    assert await _publish(api) == 2
    assert await _publish(api) == 0  # ya publicados

    global_events = [json.loads(f["data"]) for _, f in await _entries(api.redis, GLOBAL_STREAM)]
    assert len(global_events) == 2
    ev = next(e for e in global_events if e["org_id"] == str(org_a))
    assert set(ev) == {
        "id",
        "type",
        "schema_version",
        "source",
        "org_id",
        "correlation_id",
        "occurred_at",
        "payload",
    }
    assert ev["type"] == "thing.happened" and ev["schema_version"] == 1 and ev["source"] == "klick"
    assert ev["payload"] == {"n": 1}

    a_events = [json.loads(f["data"]) for _, f in await _entries(api.redis, org_stream(org_a))]
    b_events = [json.loads(f["data"]) for _, f in await _entries(api.redis, org_stream(org_b))]
    assert [e["payload"]["n"] for e in a_events] == [1]  # cada organización ve solo lo suyo
    assert [e["payload"]["n"] for e in b_events] == [2]


async def test_outbox_event_is_atomic_with_the_change(api: ApiHarness, users: Users) -> None:
    _, org_a, _, _ = await _two_orgs(api, users)
    db: Database = api.app.state.db
    with pytest.raises(RuntimeError):
        async with db.tenant_session(org_a) as s:
            await emit(
                s,
                type="lost.event",
                org_id=org_a,
                aggregate_type="x",
                aggregate_id=None,
                payload={},
            )
            raise RuntimeError("falla la transacción")
    assert await _publish(api) == 0  # el evento se revirtió junto con el cambio


async def test_outbox_failure_leaves_events_pending_for_retry(
    api: ApiHarness, users: Users
) -> None:
    _, org_a, _, _ = await _two_orgs(api, users)
    await _emit(api, org_a)

    class Broken:
        def pipeline(self) -> Any:
            raise ConnectionError("redis caído")

    with pytest.raises(ConnectionError):
        await publish_pending(api.app.state.db, Broken(), 100)  # type: ignore[arg-type]
    assert await _publish(api) == 1  # al recuperarse, se publica (al menos una vez)


async def test_outbox_preserves_order_and_batches(api: ApiHarness, users: Users) -> None:
    _, org_a, _, _ = await _two_orgs(api, users)
    for n in range(5):
        await _emit(api, org_a, n=n)
    assert await publish_pending(api.app.state.db, api.redis, 3) == 3
    assert await publish_pending(api.app.state.db, api.redis, 3) == 2
    got = [
        json.loads(f["data"])["payload"]["n"]
        for _, f in await _entries(api.redis, org_stream(org_a))
    ]
    assert got == [0, 1, 2, 3, 4]


# --------------------------------------------------------------------------------- SSE


def test_last_event_id_parsing() -> None:
    assert parse_last_event_id("1700000000000-3") == "1700000000000-3"
    for bad in (None, "", "abc", "1-2-3", "1700000000000", "$"):
        assert parse_last_event_id(bad) == "$"


async def _collect(gen: AsyncIterator[str], want: int, within_s: float = 5) -> list[str]:
    frames: list[str] = []

    async def run() -> None:
        async for frame in gen:
            if frame.startswith("id:"):
                frames.append(frame)
                if len(frames) >= want:
                    return

    await asyncio.wait_for(run(), within_s)
    return frames


async def test_sse_stream_only_delivers_new_events_of_its_org(
    api: ApiHarness, users: Users
) -> None:
    _, org_a, _, org_b = await _two_orgs(api, users)
    await publish_event(
        api.redis,
        envelope(
            event_id=uuid.uuid4(),
            type="old",
            org_id=org_a,
            correlation_id=None,
            occurred_at=datetime.now(UTC),
            payload={},
        ),
    )
    gen = sse_stream(api.redis, org_a, keepalive_s=0.2)
    assert await gen.__anext__() == "retry: 3000\n\n"
    now = datetime.now(UTC)
    for org, n in ((org_b, 1), (org_a, 2), (org_a, 3)):
        await publish_event(
            api.redis,
            envelope(
                event_id=uuid.uuid4(),
                type="evt",
                org_id=org,
                correlation_id=None,
                occurred_at=now,
                payload={"n": n},
            ),
        )
    frames = await _collect(gen, 2)
    await gen.aclose()
    payloads = [json.loads(f.split("data: ", 1)[1].strip())["payload"]["n"] for f in frames]
    assert payloads == [2, 3]  # ni lo viejo ni lo de la otra organización
    assert all(f.startswith("id: ") and "\nevent: evt\n" in f for f in frames)


async def test_sse_resumes_from_last_event_id_and_sends_keepalives(
    api: ApiHarness, users: Users
) -> None:
    _, org_a, _, _ = await _two_orgs(api, users)
    now = datetime.now(UTC)
    for n in range(3):
        await publish_event(
            api.redis,
            envelope(
                event_id=uuid.uuid4(),
                type="evt",
                org_id=org_a,
                correlation_id=None,
                occurred_at=now,
                payload={"n": n},
            ),
        )
    first_id = (await _entries(api.redis, org_stream(org_a)))[0][0]
    frames = await _collect(sse_stream(api.redis, org_a, last_event_id=first_id), 2)
    assert [json.loads(f.split("data: ", 1)[1])["payload"]["n"] for f in frames] == [1, 2]

    idle = [f async for f in sse_stream(api.redis, org_a, keepalive_s=0.1, max_idle_cycles=2)]
    assert idle == ["retry: 3000\n\n", ": keepalive\n\n"]


# ------------------------------------------------------- endpoint SSE sobre un servidor real


@asynccontextmanager
async def _serve(app: FastAPI) -> AsyncIterator[str]:
    """Levanta uvicorn en un puerto libre (el ciclo de vida ya lo gestiona la fixture)."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


def _cookie_header(actor: Actor) -> dict[str, str]:
    return {"Cookie": f"klick_session={actor.client.cookies['klick_session']}"}


async def test_sse_endpoint_requires_auth_and_membership(api: ApiHarness, users: Users) -> None:
    ana, org_a, bruno, org_b = await _two_orgs(api, users)
    anon = await api.client.get(f"{BASE}/events/stream", params={"org_id": str(org_a)})
    assert anon.status_code == 401
    no_org = await ana.client.get(f"{BASE}/events/stream")
    assert no_org.status_code == 400 and no_org.json()["code"] == "org_required"
    foreign = await ana.client.get(f"{BASE}/events/stream", params={"org_id": str(org_b)})
    assert foreign.status_code == 403
    foreign_header = await ana.call("GET", "/events/stream", org=org_b)
    assert foreign_header.status_code == 403


async def test_system_ping_progress_arrives_live_over_sse(api: ApiHarness, users: Users) -> None:
    """Criterio de aceptación de F0: el job system.ping muestra progreso en vivo por SSE."""
    ana, org_a, _, _ = await _two_orgs(api, users)
    seen: list[dict[str, Any]] = []
    connected = asyncio.Event()

    async with _serve(api.app) as base:

        async def listen() -> None:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                async with client.stream(
                    "GET",
                    f"{base}{BASE}/events/stream",
                    params={"org_id": str(org_a)},
                    headers=_cookie_header(ana),
                ) as resp:
                    assert resp.status_code == 200
                    assert resp.headers["content-type"].startswith("text/event-stream")
                    event = ""
                    async for line in resp.aiter_lines():
                        if line.startswith("retry:"):
                            connected.set()
                        elif line.startswith("event:"):
                            event = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line.split(":", 1)[1])
                            seen.append({"event": event, **data})
                            if event == "job.succeeded":
                                return

        listener = asyncio.create_task(listen())
        await asyncio.wait_for(connected.wait(), 10)

        created = await ana.call(
            "POST", "/jobs", org=str(org_a), json={"kind": "system.ping", "payload": {"steps": 4}}
        )
        job_id = created.json()["job_id"]
        deps = WorkerDeps(
            db=api.app.state.db,
            redis=api.redis,
            settings=api.settings,
            dispatcher=api.dispatcher,
            worker_id="sse-test",
        )
        assert await execute_job(deps, uuid.UUID(job_id), org_a) == "succeeded"
        await _publish(api)  # el scheduler lo haría cada segundo
        await asyncio.wait_for(listener, 15)

    progress = [e["payload"]["progress"] for e in seen if e["event"] == "job.progress"]
    assert progress == [25, 50, 75, 100]
    kinds = [e["event"] for e in seen]
    assert "job.created" in kinds and kinds[-1] == "job.succeeded"
    assert all(e["org_id"] == str(org_a) for e in seen)
    assert all(e["payload"]["job_id"] == job_id for e in seen)
