import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from actors import Users, create_org
from fastapi import FastAPI
from harness import ApiHarness
from klick.core.config import Settings
from klick.core.logging import trace_context
from klick.core.telemetry import setup_tracing
from klick.main import create_app
from opentelemetry.sdk.trace import TracerProvider
from pydantic import SecretStr


def _prod_settings(**kw: object) -> Settings:
    return Settings(
        environment="production",
        app_secret_key=SecretStr("prod-app-secret-0123456789abcdef0123456789"),
        encryption_master_key=SecretStr("prod-encryption-key-0123456789abcdef0123"),
        s3_secret_key=SecretStr("prod-s3-secret-0123456789"),
        **kw,  # type: ignore[arg-type]
    )


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(environment="test"))

    @app.get("/_test/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    async for c in _client(app):
        yield c


def _sample(text: str, name: str, **labels: str) -> float | None:
    """Valor de una muestra de la salida de texto de Prometheus (None si no existe)."""
    for line in text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            if all(f'{k}="{v}"' in line for k, v in labels.items()):
                return float(line.rsplit(" ", 1)[1])
    return None


async def test_metrics_count_requests_by_route_template(client: httpx.AsyncClient) -> None:
    await client.get("/healthz")
    await client.get("/healthz")
    random_id = uuid.uuid4()
    await client.get(f"/_test/items/{random_id}")
    await client.get(f"/no-existe/{random_id}")
    body = (await client.get("/metrics")).text
    assert _sample(body, "klick_http_requests_total", route="/healthz", status="200") == 2
    # La etiqueta usa la plantilla, jamás el identificador real.
    assert str(random_id) not in body
    assert _sample(body, "klick_http_requests_total", route="/_test/items/{item_id}") == 1
    # Las rutas inexistentes se agrupan en una sola etiqueta (baja cardinalidad).
    assert _sample(body, "klick_http_requests_total", route="unmatched", status="404") == 1
    assert _sample(body, "klick_http_request_duration_seconds_count", route="/healthz") == 2


async def test_metrics_endpoint_does_not_measure_itself_and_serves_text(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert 'route="/metrics"' not in (await client.get("/metrics")).text


async def test_metrics_survive_database_outage(client: httpx.AsyncClient) -> None:
    # Sin lifespan no hay base de datos: /metrics sigue respondiendo y lo declara.
    body = (await client.get("/metrics")).text
    assert _sample(body, "klick_jobs_scrape_ok") == 0


async def test_metrics_are_not_part_of_the_public_contract(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/v1/openapi.json")).json()
    assert "/metrics" not in spec["paths"]


async def test_metrics_in_production_require_the_token() -> None:
    app = create_app(_prod_settings(metrics_token=SecretStr("tok-123")))
    async for c in _client(app):
        assert (await c.get("/metrics")).status_code == 401
        assert (
            await c.get("/metrics", headers={"Authorization": "Bearer nope"})
        ).status_code == 401
        assert (
            await c.get("/metrics", headers={"Authorization": "Basic tok-123"})
        ).status_code == 401
        ok = await c.get("/metrics", headers={"Authorization": "Bearer tok-123"})
        assert ok.status_code == 200


async def test_metrics_in_production_are_disabled_without_a_token() -> None:
    app = create_app(_prod_settings())
    async for c in _client(app):
        assert (await c.get("/metrics")).status_code == 404
        assert (await c.get("/metrics", headers={"Authorization": "Bearer "})).status_code == 404


def test_tracing_is_off_without_endpoint() -> None:
    app = create_app(Settings(environment="test"))
    assert setup_tracing(app, Settings(environment="test")) is None


def test_tracing_instruments_and_shuts_down_cleanly() -> None:
    settings = Settings(environment="test", otel_exporter_otlp_endpoint="http://127.0.0.1:1")
    app = create_app(settings)
    assert app.state.stop_tracing is not None
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True
    app.state.stop_tracing()
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is False


def test_log_lines_carry_trace_ids_only_inside_a_span() -> None:
    assert "trace_id" not in trace_context(None, "info", {"event": "x"})
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        out = trace_context(None, "info", {"event": "x"})
        ctx = span.get_span_context()
        assert out["trace_id"] == format(ctx.trace_id, "032x")
        assert out["span_id"] == format(ctx.span_id, "016x")


@pytest.mark.db
async def test_job_gauges_are_aggregates_without_tenant_data(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    org = await create_org(ana)
    for _ in range(2):
        r = await ana.call(
            "POST", "/jobs", org=org["id"], json={"kind": "system.ping", "payload": {"steps": 1}}
        )
        assert r.status_code == 202, r.text

    body = (await api.client.get("/metrics")).text
    assert _sample(body, "klick_jobs_scrape_ok") == 1
    assert _sample(body, "klick_jobs", kind="system.ping", status="queued") == 2
    assert (_sample(body, "klick_jobs_oldest_queued_age_seconds") or 0) >= 0
    # Solo agregados: ni la organización ni el usuario aparecen en la salida.
    assert org["id"] not in body
    assert "ana@example.com" not in body
