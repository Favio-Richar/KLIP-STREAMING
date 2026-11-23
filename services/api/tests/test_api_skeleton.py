from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from klick.core.config import Settings
from klick.core.errors import ApiError
from klick.core.health import clear_readiness_checks, register_readiness_check
from klick.main import create_app


@pytest.fixture
def app() -> FastAPI:
    clear_readiness_checks()
    app = create_app(Settings(environment="test", cors_allow_origins=["http://localhost:3000"]))

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    @app.get("/_test/conflict")
    async def conflict() -> None:
        raise ApiError(409, "idempotency_conflict", "Conflict", "Body differs")

    @app.get("/_test/typed")
    async def typed(n: int) -> dict[str, int]:
        return {"n": n}

    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_reports_failing_dependency(client: httpx.AsyncClient) -> None:
    async def ok() -> None:
        return None

    async def down() -> None:
        raise ConnectionError("db password=hunter2")

    register_readiness_check("ok", ok)
    r = await client.get("/readyz")
    assert r.status_code == 200
    register_readiness_check("db", down)
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"] == {"ok": "ok", "db": "unavailable"}
    assert "hunter2" not in r.text


async def test_request_id_generated_and_echoed(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.headers["x-request-id"]
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


async def test_not_found_is_problem_json(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/nope", headers={"X-Request-ID": "rid-1"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "not_found"
    assert body["status"] == 404
    assert body["request_id"] == "rid-1"


async def test_validation_error_lists_fields(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/typed", params={"n": "x"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["field"] == "n"


async def test_api_error_uses_stable_code(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/conflict")
    assert r.status_code == 409
    assert r.json()["code"] == "idempotency_conflict"
    assert r.json()["detail"] == "Body differs"


async def test_unhandled_error_hides_internals(client: httpx.AsyncClient) -> None:
    r = await client.get("/_test/boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "secret internals" not in r.text
    assert r.json()["code"] == "internal_error"
    assert r.headers["x-request-id"]


async def test_openapi_published(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "Klick API"


async def test_cors_preflight(client: httpx.AsyncClient) -> None:
    r = await client.options(
        "/healthz",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    r = await client.options(
        "/healthz",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in r.headers
