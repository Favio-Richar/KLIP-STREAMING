import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from actors import Users, create_org
from fastapi import Request
from harness import ApiHarness
from klick.core.errors import ApiError
from klick.core.idempotency import IdempotencyStore, request_hash, validate_key
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


def _request(key: str | None = "k1", method: str = "POST") -> Request:
    headers = [(b"idempotency-key", key.encode())] if key else []
    return Request({"type": "http", "method": method, "headers": headers, "path": "/x"})


def _store(api: ApiHarness) -> IdempotencyStore:
    store: IdempotencyStore = api.app.state.idempotency
    return store


async def _org(api: ApiHarness, users: Users, email: str = "ana@example.com") -> uuid.UUID:
    ana = await users.make(email)
    return uuid.UUID((await create_org(ana, f"Org {email}"))["id"])


def test_request_hash_is_stable_and_sensitive() -> None:
    a = request_hash("POST", "s", {"a": 1, "b": [1, 2]})
    assert a == request_hash("POST", "s", {"b": [1, 2], "a": 1})  # independiente del orden
    assert a != request_hash("POST", "s", {"a": 2, "b": [1, 2]})
    assert a != request_hash("PUT", "s", {"a": 1, "b": [1, 2]})
    assert a != request_hash("POST", "other", {"a": 1, "b": [1, 2]})


@pytest.mark.parametrize("bad", ["", "x" * 256, "ñandú", "con\nsalto"])
def test_invalid_keys_are_rejected(bad: str) -> None:
    with pytest.raises(ApiError) as err:
        validate_key(bad)
    assert err.value.code == "invalid_idempotency_key"


async def test_without_header_the_producer_always_runs(api: ApiHarness, users: Users) -> None:
    org = await _org(api, users)
    calls = []

    async def produce() -> tuple[int, Any]:
        calls.append(1)
        return 201, {"n": len(calls)}

    store = _store(api)
    r1 = await store.run(_request(None), org, "s", {}, produce)
    r2 = await store.run(_request(None), org, "s", {}, produce)
    assert (r1.status_code, r2.status_code, len(calls)) == (201, 201, 2)


async def test_same_key_runs_once_and_replays(api: ApiHarness, users: Users) -> None:
    org = await _org(api, users)
    calls = []

    async def produce() -> tuple[int, Any]:
        calls.append(1)
        return 201, {"n": len(calls)}

    store = _store(api)
    first = await store.run(_request(), org, "s", {"a": 1}, produce)
    second = await store.run(_request(), org, "s", {"a": 1}, produce)
    assert len(calls) == 1
    assert first.body == second.body and second.status_code == 201
    assert second.headers["idempotent-replayed"] == "true"
    with pytest.raises(ApiError) as err:
        await store.run(_request(), org, "s", {"a": 2}, produce)
    assert err.value.status == 409 and err.value.code == "idempotency_key_reuse"
    assert len(calls) == 1


async def test_keys_are_scoped_by_org_and_scope(api: ApiHarness, users: Users) -> None:
    org_a = await _org(api, users, "ana@example.com")
    org_b = await _org(api, users, "bruno@example.com")
    calls = []

    async def produce() -> tuple[int, Any]:
        calls.append(1)
        return 200, {}

    store = _store(api)
    await store.run(_request(), org_a, "s", {}, produce)
    await store.run(_request(), org_b, "s", {}, produce)  # otra organización: no se repite
    await store.run(_request(), org_a, "otra", {}, produce)  # otro scope
    assert len(calls) == 3


async def test_failures_are_not_cached(api: ApiHarness, users: Users) -> None:
    org = await _org(api, users)
    attempts = []

    async def produce() -> tuple[int, Any]:
        attempts.append(1)
        if len(attempts) == 1:
            raise ApiError(422, "boom", "Boom")
        return 201, {"ok": True}

    store = _store(api)
    with pytest.raises(ApiError):
        await store.run(_request(), org, "s", {}, produce)
    assert (await _sql(api, "select count(*) as n from idempotency_keys"))[0]["n"] == 0
    ok = await store.run(_request(), org, "s", {}, produce)
    assert ok.status_code == 201 and len(attempts) == 2


async def test_concurrent_duplicates_run_once_and_the_other_gets_409(
    api: ApiHarness, users: Users
) -> None:
    org = await _org(api, users)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def slow() -> tuple[int, Any]:
        calls.append(1)
        started.set()
        await release.wait()
        return 201, {"done": True}

    store = _store(api)
    first = asyncio.create_task(store.run(_request(), org, "s", {}, slow))
    await asyncio.wait_for(started.wait(), 5)
    with pytest.raises(ApiError) as err:
        await store.run(_request(), org, "s", {}, slow)
    assert err.value.code == "idempotency_in_progress"
    assert err.value.headers == {"Retry-After": "1"}
    release.set()
    assert (await first).status_code == 201
    replay = await store.run(_request(), org, "s", {}, slow)
    assert replay.status_code == 201 and len(calls) == 1


async def test_stale_in_progress_and_expired_keys_can_be_retaken(
    api: ApiHarness, users: Users
) -> None:
    org = await _org(api, users)
    store = _store(api)
    calls = []

    async def produce() -> tuple[int, Any]:
        calls.append(1)
        return 200, {"n": len(calls)}

    # Un intento anterior murió sin cerrar: tras 5 minutos se retoma.
    old = datetime.now(UTC) - timedelta(minutes=10)
    await _sql(
        api,
        "insert into idempotency_keys (org_id, scope, key, request_hash, status, created_at, expires_at) "
        "values ($1, 's', 'k1', $2, 'in_progress', $3, now() + interval '1 day')",
        org,
        request_hash("POST", "s", {}),
        old,
    )
    assert (await store.run(_request(), org, "s", {}, produce)).status_code == 200
    # Una clave vencida se vuelve a ejecutar.
    await _sql(api, "update idempotency_keys set expires_at = now() - interval '1 minute'")
    again = await store.run(_request(), org, "s", {}, produce)
    assert again.status_code == 200 and len(calls) == 2
    assert "idempotent-replayed" not in again.headers


async def test_invitation_creation_is_idempotent_over_http(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    org = (await create_org(ana))["id"]
    body = {"email": "nuevo@example.com", "role": "viewer"}
    h = {"Idempotency-Key": "inv-1"}
    first = await ana.call("POST", "/invitations", org=org, json=body, headers=h)
    emails_after_first = len(api.emails.sent)
    second = await ana.call("POST", "/invitations", org=org, json=body, headers=h)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert len(api.emails.sent) == emails_after_first  # la repetición no reenvía el correo
    assert len((await ana.call("GET", "/invitations", org=org)).json()) == 1
