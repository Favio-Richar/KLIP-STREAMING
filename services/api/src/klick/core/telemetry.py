"""Observabilidad: métricas Prometheus (`/metrics`) y trazas OpenTelemetry opcionales.

Reglas:
- Las etiquetas usan la *plantilla* de la ruta (`/api/v1/jobs/{job_id}`), nunca la URL real, y
  nunca identificadores de organización ni de usuario: baja cardinalidad y sin datos de clientes.
- Los gauges de jobs son agregados globales (tipo y estado) leídos con el rol de sistema.
- Cada aplicación tiene su propio registro: `create_app` puede llamarse varias veces (pruebas).
"""

import hmac
import time
from collections.abc import Callable

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client import generate_latest as _generate_latest
from sqlalchemy import func, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from klick.core.config import Settings
from klick.core.errors import ApiError
from klick.core.jobs import Job

log = structlog.get_logger("klick.telemetry")

UNMATCHED = "unmatched"
METRICS_PATH = "/metrics"
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "klick_http_requests_total",
            "Peticiones HTTP atendidas",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "klick_http_request_duration_seconds",
            "Duración de las peticiones HTTP",
            ["method", "route"],
            buckets=_BUCKETS,
            registry=self.registry,
        )
        self.in_flight = Gauge(
            "klick_http_requests_in_flight", "Peticiones en curso", registry=self.registry
        )
        self.jobs = Gauge(
            "klick_jobs",
            "Jobs por tipo y estado (todas las organizaciones, solo agregados)",
            ["kind", "status"],
            registry=self.registry,
        )
        self.oldest_queued = Gauge(
            "klick_jobs_oldest_queued_age_seconds",
            "Antigüedad del job en cola más viejo",
            registry=self.registry,
        )
        self.jobs_scrape_ok = Gauge(
            "klick_jobs_scrape_ok",
            "1 si la última lectura de jobs desde la base de datos funcionó",
            registry=self.registry,
        )


class MetricsMiddleware:
    """ASGI puro: mide cada petición HTTP (excepto el propio /metrics)."""

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == METRICS_PATH:
            await self.app(scope, receive, send)
            return
        m = self.metrics
        status = {"code": 500}
        started = time.perf_counter()
        m.in_flight.inc()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            m.in_flight.dec()
            route = getattr(scope.get("route"), "path", None) or UNMATCHED
            method = scope["method"]
            m.requests.labels(method, route, str(status["code"])).inc()
            m.duration.labels(method, route).observe(time.perf_counter() - started)


router = APIRouter(tags=["observability"])


async def _refresh_job_gauges(request: Request) -> None:
    m: Metrics = request.app.state.metrics
    try:
        async with request.app.state.db.system_session() as session:
            rows = (
                await session.execute(
                    select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)
                )
            ).all()
            oldest = (
                await session.execute(
                    select(func.extract("epoch", func.now() - func.min(Job.created_at))).where(
                        Job.status == "queued"
                    )
                )
            ).scalar()
    except Exception:  # noqa: BLE001 - /metrics debe seguir respondiendo aunque la base falle
        m.jobs_scrape_ok.set(0)
        log.warning("metrics_jobs_scrape_failed")
        return
    m.jobs.clear()
    for kind, status, count in rows:
        m.jobs.labels(kind, status).set(count)
    m.oldest_queued.set(float(oldest or 0))
    m.jobs_scrape_ok.set(1)


@router.get(METRICS_PATH, include_in_schema=False)
async def metrics(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    if settings.is_production:
        expected = settings.metrics_token.get_secret_value()
        if not expected:
            raise ApiError(404, "not_found", "Resource not found", "Metrics are disabled.")
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
            raise ApiError(401, "unauthorized", "Unauthorized", "Invalid metrics credentials.")
    await _refresh_job_gauges(request)
    m: Metrics = request.app.state.metrics
    return Response(_generate_latest(m.registry), media_type=CONTENT_TYPE_LATEST)


def setup_tracing(app: FastAPI, settings: Settings) -> Callable[[], None] | None:
    """Instrumenta la app con OpenTelemetry si hay endpoint OTLP. Devuelve la función de cierre."""
    if not settings.otel_exporter_otlp_endpoint:
        return None
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: settings.otel_service_name,
                "deployment.environment": settings.environment,
            }
        )
    )
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, excluded_urls="healthz,readyz,metrics"
    )

    def shutdown() -> None:
        FastAPIInstrumentor.uninstrument_app(app)
        provider.shutdown()

    log.info("tracing_enabled", service=settings.otel_service_name)
    return shutdown
