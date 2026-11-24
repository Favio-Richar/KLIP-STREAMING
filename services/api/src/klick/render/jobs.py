"""Job ``render`` (F1-09): ejecuta el plan de FFmpeg de una versión inmutable de un clip.

Pasos: cargar la versión → validar → descargar las fuentes → (medir volumen) → renderizar con
progreso → subir el resultado → registrar el asset exportado y el consumo → listo.

Robustez (docs/07 §3, docs/11 §3):
- **Idempotente y reanudable:** si el worker cae, el reconciliador reencola el job completo. El
  objeto de salida tiene clave fija (se sobrescribe) y el asset, el consumo y el estado final se
  confirman en una sola transacción: nunca queda un render a medias, duplicado ni cobrado dos veces.
- **Cancelable:** la señal llega a FFmpeg (se mata el proceso) y se limpian los temporales.
- El mensaje de FFmpeg no llega al usuario: solo un código estable en ``render_jobs.error``.
"""

import asyncio
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from klick.clips.models import Clip
from klick.clips.service import ClipService
from klick.core.config import Settings
from klick.core.events import publish_ephemeral
from klick.core.ids import new_id
from klick.core.jobs import JobCanceledError, JobService, PermanentJobError, job_kind
from klick.core.outbox import emit
from klick.core.worker import JobContext
from klick.edit_project import (
    EditProjectInvalid,
    MigrationError,
    migrate_edit_project,
    parse_edit_project,
)
from klick.edit_project.models import EditProject
from klick.media import ffmpeg as ff
from klick.media.models import MediaAsset
from klick.media.service import MediaService
from klick.render.exprs import RenderPlanError
from klick.render.graph import (
    LoudnormMeasure,
    RenderInputs,
    RenderPlan,
    SourceInfo,
    build_render_plan,
    parse_loudnorm,
)
from klick.render.models import RenderJob, RenderPresetRow, UsageRecord
from klick.render.presets import Preset
from klick.render.service import RenderService
from klick.storage.backend import S3Backend
from klick.storage.keys import render_output
from klick.storage.service import StorageService

log = structlog.get_logger("klick.render")

DISK_HEADROOM = 3.0


class RenderRejected(Exception):
    """El render no se puede completar por una causa permanente (documento, fuentes, límites)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _scratch(settings: Settings, job_id: uuid.UUID) -> Path:
    root = (
        Path(settings.media_scratch_dir)
        if settings.media_scratch_dir
        else Path(tempfile.gettempdir())
    )
    return root / f"klick-render-{job_id}"


def _preset_of(row: RenderPresetRow) -> Preset:
    return Preset(
        key=row.key,
        width=row.width,
        height=row.height,
        fps=row.fps,
        crf=row.crf,
        max_bitrate_kbps=row.max_bitrate_kbps,
        audio_bitrate_kbps=row.audio_bitrate_kbps,
        loudness_target_lufs=float(row.loudness_target_lufs),
        container=row.container,
        video_codec=row.video_codec,
        audio_codec=row.audio_codec,
    )


async def _ffmpeg_version(ffmpeg: str) -> str:
    result = await ff.run([ffmpeg, "-version"], timeout_s=20, capture_stdout=True)
    first = result.stdout.splitlines()[0] if result.stdout else ""
    parts = first.split()
    return parts[2][:50] if len(parts) > 2 else "unknown"


class _Reporter:
    """Progreso del render: job + evento `render.progress` por SSE + fila de `render_jobs`."""

    def __init__(self, ctx: JobContext, render_job_id: uuid.UUID) -> None:
        self.ctx, self.rid = ctx, render_job_id
        self._last = -1

    async def set(self, percent: float, message: str | None = None) -> None:
        value = max(0, min(100, int(percent)))
        if value == self._last:
            return
        self._last = value
        await self.ctx.progress(value, message)
        async with self.ctx.db.tenant_session(self.ctx.org_id) as s:  # type: ignore[arg-type]
            await s.execute(
                update(RenderJob)
                .where(RenderJob.id == self.rid, RenderJob.status == "running")
                .values(progress=value)
            )
        if self.ctx.org_id:
            await publish_ephemeral(
                self.ctx.redis,
                org_id=self.ctx.org_id,
                type="render.progress",
                correlation_id=self.ctx.job.correlation_id,
                payload={"render_job_id": str(self.rid), "progress": value},
            )


@job_kind("render", queue="media", max_attempts=3, timeout_s=4 * 3600)
async def render_clip(ctx: JobContext) -> dict[str, Any]:
    try:
        render_job_id = uuid.UUID(str(ctx.payload["render_job_id"]))
    except (KeyError, ValueError):
        raise PermanentJobError("render_job_id inválido") from None
    if ctx.org_id is None:
        raise PermanentJobError("render requiere organización")
    org_id = ctx.org_id

    storage = StorageService(ctx.db, S3Backend(ctx.settings), ctx.settings)
    jobs = JobService(ctx.db, ctx.dispatcher)  # el análisis del resultado se despacha de inmediato
    media = MediaService(ctx.db, storage, jobs)
    clips = ClipService(ctx.db, media)
    service = RenderService(ctx.db, clips, jobs)

    loaded = await service.load_for_worker(org_id, render_job_id)
    if loaded is None:
        return {"skipped": "render_job_missing"}
    job, version, clip, preset_row = loaded
    if job.status == "succeeded":
        return {"skipped": "already_succeeded"}  # un reintento tras una caída posterior al éxito
    if job.status == "canceled":
        raise JobCanceledError()

    await _set_state(ctx, render_job_id, status="running", progress=0, started=True)
    scratch = _scratch(ctx.settings, ctx.job_id)
    scratch.mkdir(parents=True, exist_ok=False)
    try:
        return await _render(ctx, media, storage, job, version.document, clip, preset_row, scratch)
    except (ff.MediaCanceled, JobCanceledError):
        await _set_state(ctx, render_job_id, status="canceled", finished=True)
        raise JobCanceledError() from None
    except RenderRejected as exc:
        log.info("render_rejected", render_job_id=str(render_job_id), code=exc.code)
        await _set_state(
            ctx, render_job_id, status="failed", error={"code": exc.code}, finished=True
        )
        raise PermanentJobError(exc.code) from None
    except Exception:
        if (
            ctx.attempt >= ctx.job.max_attempts
        ):  # último intento: no se queda "running" para siempre
            await _set_state(
                ctx, render_job_id, status="failed", error={"code": "render_failed"}, finished=True
            )
        else:
            await _set_state(ctx, render_job_id, status="queued", progress=0)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _set_state(
    ctx: JobContext,
    render_job_id: uuid.UUID,
    *,
    status: str,
    progress: int | None = None,
    error: dict[str, Any] | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    values: dict[str, Any] = {"status": status, "error": error}
    if progress is not None:
        values["progress"] = progress
    if started:
        values["started_at"] = datetime.now(UTC)
    if finished:
        values["finished_at"] = datetime.now(UTC)
    async with ctx.db.tenant_session(ctx.org_id) as s:  # type: ignore[arg-type]
        await s.execute(
            update(RenderJob)
            .where(RenderJob.id == render_job_id, RenderJob.status.notin_(("succeeded",)))
            .values(**values)
        )
        if status in ("failed", "canceled") and ctx.org_id:
            await emit(
                s,
                type=f"render.{status}",
                org_id=ctx.org_id,
                aggregate_type="render_job",
                aggregate_id=render_job_id,
                payload={"id": str(render_job_id), **({"code": error["code"]} if error else {})},
            )


async def _render(
    ctx: JobContext,
    media: MediaService,
    storage: StorageService,
    job: RenderJob,
    raw_document: dict[str, Any],
    clip: Clip,
    preset_row: RenderPresetRow,
    scratch: Path,
) -> dict[str, Any]:
    s = ctx.settings
    org_id = ctx.org_id
    assert org_id is not None  # noqa: S101 - validado por el llamador
    reporter = _Reporter(ctx, job.id)

    # 1) documento: migrar a la versión actual y validar de nuevo (nunca se confía en lo guardado)
    try:
        doc: EditProject = parse_edit_project(migrate_edit_project(raw_document))
    except (EditProjectInvalid, MigrationError):
        raise RenderRejected("invalid_document") from None
    if doc.captions is not None:
        raise RenderRejected("captions_unavailable")

    # 2) fuentes: assets vivos y listos de esta organización
    assets = await media.assets_by_ids(org_id, {uuid.UUID(src.asset_id) for src in doc.sources})
    sources: dict[str, SourceInfo] = {}
    total_bytes = 0
    await reporter.set(1, "Descargando fuentes")
    for source in doc.sources:
        asset: MediaAsset | None = assets.get(uuid.UUID(source.asset_id))
        if asset is None or asset.status != "ready":
            raise RenderRejected("source_unavailable")
        obj = await storage.get(org_id, asset.storage_object_id)
        demuxer = ff.DEMUXERS.get(obj.mime or "")
        if demuxer is None:
            raise RenderRejected("source_unavailable")
        total_bytes += obj.size_bytes or 0
        free = shutil.disk_usage(scratch).free
        if free < total_bytes * DISK_HEADROOM:
            raise OSError(f"espacio temporal insuficiente ({free} B libres)")  # transitorio
        path = scratch / f"src_{source.id}"
        await storage.download(org_id, obj.id, path)
        sources[source.id] = SourceInfo(
            path=str(path),
            width=asset.width,
            height=asset.height,
            has_audio=bool(asset.has_audio),
            demuxer=demuxer,
        )
        await ctx.raise_if_canceled()
    await reporter.set(8, "Preparando")

    # 3) plan
    aux = scratch / "aux"
    aux.mkdir()
    output = scratch / "render.mp4"
    preset = _preset_of(preset_row)

    def inputs(measured: LoudnormMeasure | None = None) -> RenderInputs:
        return RenderInputs(
            sources=sources,
            output=str(output),
            aux_dir=str(aux),
            ffmpeg=s.ffmpeg_path,
            threads=s.media_ffmpeg_threads,
            loudnorm=measured,
        )

    try:
        plan = build_render_plan(doc, preset, inputs())
        if plan.measure_args:  # normalización de volumen: primera pasada de medición
            measured = await ff.run(
                plan.measure_args,
                timeout_s=max(600.0, plan.duration_ms / 1000 * 4),
                is_canceled=ctx.is_canceled,
            )
            if measured.returncode != 0:
                log.warning("loudnorm_measure_failed", stderr=measured.stderr_tail[-500:])
                raise ff.FfmpegFailed("falló la medición de volumen")
            plan = build_render_plan(doc, preset, inputs(parse_loudnorm(measured.stderr_tail)))
    except RenderPlanError:
        raise RenderRejected("invalid_document") from None
    for path_text, content in plan.aux_files.items():  # textos del usuario: siempre en archivos
        await asyncio.to_thread(Path(path_text).write_text, content, encoding="utf-8")
    await reporter.set(15, "Renderizando")

    # 4) render con progreso
    await _run_render(ctx, plan, reporter, s)
    if not output.exists() or output.stat().st_size == 0:
        raise ff.FfmpegFailed("el render no produjo archivo")

    # 5) subir (clave fija: reintentar sobrescribe, no duplica)
    await reporter.set(93, "Guardando")
    key = render_output(org_id, job.id)
    stored = await storage.store_file(
        org_id, key, output, mime="video/mp4", created_by=job.requested_by
    )

    # 6) asset exportado, consumo, estado y clip: todo en UNA transacción (idempotente)
    version = await _ffmpeg_version(s.ffmpeg_path)
    await _finalize(ctx, media, job, clip, preset_row, stored.id, plan, version)
    await reporter.set(100, "Listo")
    return {
        "render_job_id": str(job.id),
        "duration_ms": plan.duration_ms,
        "args_hash": plan.args_hash,
    }


async def _run_render(
    ctx: JobContext, plan: RenderPlan, reporter: _Reporter, settings: Settings
) -> None:
    duration_s = plan.duration_ms / 1000

    async def on_line(line: str) -> None:
        fraction = ff.progress_fraction(line, duration_s)
        if fraction is not None:
            await reporter.set(15 + 77 * fraction)

    result = await ff.run(
        plan.render_args,
        timeout_s=max(900.0, duration_s * 8),
        on_progress_line=on_line,
        is_canceled=ctx.is_canceled,
    )
    if result.returncode != 0:
        log.warning("render_failed", stderr=result.stderr_tail[-800:])
        raise ff.FfmpegFailed(f"ffmpeg terminó con código {result.returncode}")


async def _finalize(
    ctx: JobContext,
    media: MediaService,
    job: RenderJob,
    clip: Clip,
    preset_row: RenderPresetRow,
    object_id: uuid.UUID,
    plan: RenderPlan,
    ffmpeg_version: str,
) -> None:
    org_id = ctx.org_id
    assert org_id is not None  # noqa: S101
    async with ctx.db.tenant_session(org_id) as s:
        current = (
            await s.execute(select(RenderJob).where(RenderJob.id == job.id).with_for_update())
        ).scalar_one()
        if current.status == "succeeded":  # otro intento ya lo terminó
            return
        asset_id = current.output_asset_id
    if asset_id is None:
        asset = await media.create_render_asset(
            org_id,
            title=f"{clip.title} ({preset_row.key})",
            storage_object_id=object_id,
            project_id=clip.project_id,
            created_by=job.requested_by,
        )
        asset_id = asset.id
        await media.enqueue_probe(org_id, asset.id, round_key=f"render:{job.id}")
    async with ctx.db.tenant_session(org_id) as s:
        await s.execute(
            update(RenderJob)
            .where(RenderJob.id == job.id, RenderJob.status != "succeeded")
            .values(
                status="succeeded",
                progress=100,
                output_asset_id=asset_id,
                ffmpeg_version=ffmpeg_version,
                ffmpeg_args_hash=plan.args_hash,
                error=None,
                finished_at=datetime.now(UTC),
            )
        )
        await s.execute(
            pg_insert(UsageRecord)
            .values(
                id=new_id(),
                org_id=org_id,
                metric="render_s",
                quantity=Decimal(plan.duration_ms) / Decimal(1000),
                unit="s",
                source_type="render_job",
                source_id=job.id,
                billing_mode="platform",
                idempotency_key=f"render:{job.id}",  # reintentos no cuentan dos veces
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        await s.execute(
            update(Clip).where(Clip.id == clip.id, Clip.status == "draft").values(status="ready")
        )
        await emit(
            s,
            type="render.succeeded",
            org_id=org_id,
            aggregate_type="render_job",
            aggregate_id=job.id,
            payload={"id": str(job.id), "output_asset_id": str(asset_id)},
        )
