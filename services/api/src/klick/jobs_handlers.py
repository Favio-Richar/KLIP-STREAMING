"""Manejadores de job. Importar este módulo registra todos los kinds (API y workers)."""

import asyncio
from typing import Any

from klick.core.jobs import PermanentJobError, job_kind
from klick.core.worker import JobContext

MAX_STEPS = 50
MAX_STEP_DELAY_MS = 5000


@job_kind("system.ping", queue="default", required_permission="org:manage", public=True)
async def system_ping(ctx: JobContext) -> dict[str, Any]:
    """Job de demostración: avanza en pasos con progreso en vivo (SSE). Sirve de prueba de humo
    del sistema de jobs: payload ``{steps, delay_ms, fail_until_attempt, fail_permanently}``."""
    steps = int(ctx.payload.get("steps", 5))
    delay_ms = int(ctx.payload.get("delay_ms", 0))
    if not 1 <= steps <= MAX_STEPS or not 0 <= delay_ms <= MAX_STEP_DELAY_MS:
        raise PermanentJobError("parámetros fuera de rango")
    if ctx.payload.get("fail_permanently"):
        raise PermanentJobError("fallo permanente solicitado")
    if ctx.attempt <= int(ctx.payload.get("fail_until_attempt", 0)):
        raise RuntimeError(f"fallo transitorio solicitado (intento {ctx.attempt})")
    for step in range(1, steps + 1):
        await ctx.progress(step * 100 // steps, f"paso {step} de {steps}")
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
    return {"pong": True, "steps": steps, "worker": ctx.worker_id, "attempt": ctx.attempt}
