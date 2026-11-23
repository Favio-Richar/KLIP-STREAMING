"""Sondas de salud: /healthz (proceso vivo) y /readyz (dependencias listas)."""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

ReadinessCheck = Callable[[], Awaitable[None]]


def register_readiness_check(app: FastAPI, name: str, check: ReadinessCheck) -> None:
    """La capa de datos, Redis y el almacenamiento registran aquí su sonda (por aplicación)."""
    checks: dict[str, ReadinessCheck] = app.state.readiness_checks
    checks[name] = check


router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness")
async def readyz(request: Request) -> JSONResponse:
    checks: dict[str, ReadinessCheck] = request.app.state.readiness_checks
    results: dict[str, str] = {}
    for name, check in checks.items():
        try:
            await check()
            results[name] = "ok"
        except Exception:  # noqa: BLE001 - el detalle no se expone al cliente
            results[name] = "unavailable"
    ready = all(v == "ok" for v in results.values())
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": results},
        status_code=200 if ready else 503,
    )
