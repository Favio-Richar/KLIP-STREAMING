"""Job ``media.probe`` (F1-04): del archivo subido a un asset ``ready`` con sus derivados.

Pasos: descarga a un directorio temporal aislado del job → hash SHA-256 verificado por el servidor →
FFprobe (por contenido, con el demuxer forzado) → derivados (proxy 720p, miniatura, tira de
miniaturas, forma de onda y audio 16 kHz) → asset ``ready``.

Es idempotente y reanudable: un reintento rehace el trabajo desde cero sobre los mismos objetos
(los derivados se reemplazan, no se duplican) y un job que encuentra el asset ya terminado no hace
nada. El directorio temporal se borra siempre.
"""

import asyncio
import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import structlog

from klick.core.errors import ApiError
from klick.core.jobs import JobCanceledError, PermanentJobError, job_kind
from klick.core.worker import JobContext
from klick.media import ffmpeg as ff
from klick.media.models import MediaAsset
from klick.media.service import MediaService
from klick.storage.backend import S3Backend
from klick.storage.keys import asset_derivative
from klick.storage.service import StorageService

log = structlog.get_logger("klick.media.probe")

HASH_CHUNK = 8 * 1024 * 1024
DISK_HEADROOM = 3.0  # espacio libre exigido: original + proxy + temporales


def _scratch_dir(base: str, job_id: uuid.UUID) -> Path:
    root = Path(base) if base else Path(tempfile.gettempdir())
    return root / f"klick-probe-{job_id}"


def _sha256_file(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            digest.update(chunk)
    return digest.digest()


class _Progress:
    """Reparte el avance de cada etapa en el rango 0-100 del job, sin saturar la base."""

    def __init__(self, ctx: JobContext) -> None:
        self._ctx = ctx
        self._last = -1

    async def at(self, percent: float, message: str | None = None) -> None:
        value = int(percent)
        if value != self._last:
            self._last = value
            await self._ctx.progress(value, message)

    def stage(self, start: int, end: int) -> "_Stage":
        return _Stage(self, start, end)


class _Stage:
    def __init__(self, progress: _Progress, start: int, end: int) -> None:
        self._p, self._start, self._end = progress, start, end

    async def fraction(self, fraction: float, message: str | None = None) -> None:
        await self._p.at(
            self._start + (self._end - self._start) * max(0.0, min(1.0, fraction)), message
        )


@job_kind("media.probe", queue="media", max_attempts=3, timeout_s=4 * 3600)
async def media_probe(ctx: JobContext) -> dict[str, Any]:
    try:
        asset_id = uuid.UUID(str(ctx.payload["asset_id"]))
    except (KeyError, ValueError):
        raise PermanentJobError("asset_id inválido") from None
    if ctx.org_id is None:
        raise PermanentJobError("media.probe requiere organización")
    org_id = ctx.org_id

    storage = StorageService(ctx.db, S3Backend(ctx.settings), ctx.settings)
    media = MediaService(ctx.db, storage)
    try:
        asset = (await media.get_asset(org_id, asset_id)).asset
    except ApiError:
        return {"skipped": "asset_missing"}  # se eliminó antes de procesarse
    if asset.status != "processing":
        return {"skipped": f"status_{asset.status}"}  # ya terminado o reiniciado por otro job

    scratch = _scratch_dir(ctx.settings.media_scratch_dir, ctx.job_id)
    scratch.mkdir(parents=True, exist_ok=False)
    try:
        return await _process(ctx, media, storage, asset, scratch)
    except ff.MediaRejected as exc:
        log.info("media_rejected", asset_id=str(asset_id), code=exc.code)
        await media.transition_asset(org_id, asset_id, "failed", error={"code": exc.code})
        raise PermanentJobError(exc.code) from None
    except ff.MediaCanceled:
        raise JobCanceledError() from None
    except JobCanceledError:
        raise
    except Exception:
        if ctx.attempt >= ctx.job.max_attempts:  # último intento: el asset no se queda procesando
            await media.transition_asset(
                org_id, asset_id, "failed", error={"code": "processing_failed"}
            )
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _process(
    ctx: JobContext,
    media: MediaService,
    storage: StorageService,
    asset: MediaAsset,
    scratch: Path,
) -> dict[str, Any]:
    s = ctx.settings
    org_id = ctx.org_id
    assert org_id is not None  # noqa: S101 - validado por el llamador
    progress = _Progress(ctx)
    obj = await storage.get(org_id, asset.storage_object_id)
    mime = obj.mime or ""

    free = shutil.disk_usage(scratch).free
    if obj.size_bytes and free < obj.size_bytes * DISK_HEADROOM:
        raise OSError(f"espacio temporal insuficiente ({free} B libres)")  # transitorio: reintenta

    # 1) descarga + hash verificado
    original = scratch / "original"
    await progress.at(1, "Descargando")
    await storage.download(org_id, obj.id, original)
    await ctx.raise_if_canceled()
    digest = await asyncio.to_thread(_sha256_file, original)
    await storage.set_verified_sha256(org_id, obj.id, digest)
    await progress.at(10, "Analizando")

    # 2) análisis por contenido
    data = await ff.ffprobe(s.ffprobe_path, original, mime, timeout_s=s.media_probe_timeout_s)
    info = ff.analyze(
        data,
        asset.kind,
        ff.Limits(s.media_max_duration_s, s.media_max_dimension, s.media_max_streams),
    )
    await progress.at(15, "Generando derivados")

    # 3) derivados
    derivatives: list[str] = []
    store = _DerivativeStore(ctx, media, storage, asset, scratch, derivatives, mime)
    if info.kind == "video":
        proxy = await store.proxy(original, info, progress.stage(15, 65))
        await ctx.raise_if_canceled()
        await store.thumbnail(proxy, info, progress.stage(65, 70), store.proxy_demuxer)
        await store.sprite(proxy, info, progress.stage(70, 78))
    elif info.kind == "image":
        await store.thumbnail(original, info, progress.stage(15, 90), store.demuxer)
    if info.has_audio and info.kind in ("video", "audio"):
        await ctx.raise_if_canceled()
        await store.waveform(original, info, progress.stage(78, 88))
        await store.audio16k(original, info, progress.stage(88, 97))

    # 4) listo
    await progress.at(98, "Finalizando")
    await media.transition_asset(
        org_id,
        asset.id,
        "ready",
        probe=info.probe,
        duration_ms=info.duration_ms,
        width=info.width,
        height=info.height,
        fps=info.fps,
        video_codec=info.video_codec,
        audio_codec=info.audio_codec,
        container=info.container,
        bitrate_kbps=info.bitrate_kbps,
        has_audio=info.has_audio,
    )
    await progress.at(100, "Listo")
    return {
        "asset_id": str(asset.id),
        "duration_ms": info.duration_ms,
        "derivatives": derivatives,
        "sha256": digest.hex(),
    }


class _DerivativeStore:
    """Genera un derivado con FFmpeg, lo sube al almacén y lo registra."""

    def __init__(
        self,
        ctx: JobContext,
        media: MediaService,
        storage: StorageService,
        asset: MediaAsset,
        scratch: Path,
        done: list[str],
        mime: str,
    ) -> None:
        self.ctx, self.media, self.storage, self.asset = ctx, media, storage, asset
        self.scratch, self.done = scratch, done
        # el mismo demuxer que validó FFprobe: FFmpeg nunca vuelve a adivinar el formato
        self.demuxer = ff.DEMUXERS[mime]
        self.proxy_demuxer = ff.DEMUXERS["video/mp4"]
        self.s = ctx.settings

    async def _run(
        self,
        cmd: list[str],
        timeout_s: float,
        *,
        stage: "_Stage | None" = None,
        duration_s: float = 0,
        capture: bool = False,
    ) -> ff.RunResult:
        async def on_line(line: str) -> None:
            if stage is not None:
                fraction = ff.progress_fraction(line, duration_s)
                if fraction is not None:
                    await stage.fraction(fraction)

        result = await ff.run(
            cmd,
            timeout_s=timeout_s,
            capture_stdout=capture,
            on_progress_line=on_line if stage is not None else None,
            is_canceled=self.ctx.is_canceled,
        )
        if result.returncode != 0:
            log.warning("ffmpeg_failed", stderr=result.stderr_tail[-600:])
            raise ff.FfmpegFailed(f"ffmpeg terminó con código {result.returncode}")
        return result

    async def _register(self, kind: str, path: Path, mime: str, meta: dict[str, Any]) -> None:
        org_id = self.ctx.org_id
        assert org_id is not None  # noqa: S101
        obj = await self.storage.store_file(
            org_id,
            asset_derivative(org_id, self.asset.id, kind),
            path,
            mime=mime,
            created_by=self.asset.created_by,
        )
        await self.media.upsert_derivative(org_id, self.asset.id, kind, obj.id, meta)
        self.done.append(kind)

    def _timeout(self, duration_ms: int | None, factor: float, floor: float) -> float:
        return max(floor, (duration_ms or 0) / 1000 * factor)

    async def proxy(self, src: Path, info: ff.MediaInfo, stage: "_Stage") -> Path:
        dst = self.scratch / "proxy.mp4"
        cmd = ff.proxy_command(
            self.s.ffmpeg_path, src, dst, threads=self.s.media_ffmpeg_threads, demuxer=self.demuxer
        )
        await self._run(
            cmd,
            self._timeout(info.duration_ms, 4, 900),
            stage=stage,
            duration_s=(info.duration_ms or 0) / 1000,
        )
        height = min(720, info.height or 720)
        width = round((info.width or 0) * height / (info.height or 1) / 2) * 2
        await self._register(
            "proxy_720p",
            dst,
            "video/mp4",
            {"width": width, "height": height, "codec": "h264", "duration_ms": info.duration_ms},
        )
        await stage.fraction(1)
        return dst

    async def thumbnail(self, src: Path, info: ff.MediaInfo, stage: "_Stage", demuxer: str) -> None:
        dst = self.scratch / "thumbnail.jpg"
        at_s = min(max((info.duration_ms or 0) / 1000 * 0.1, 0), 5) if info.duration_ms else 0
        cmd = ff.thumbnail_command(
            self.s.ffmpeg_path,
            src,
            dst,
            at_s=at_s,
            threads=self.s.media_ffmpeg_threads,
            demuxer=demuxer,
        )
        await self._run(cmd, 120)
        await self._register("thumbnail", dst, "image/jpeg", {"at_ms": round(at_s * 1000)})
        await stage.fraction(1)

    async def sprite(self, src: Path, info: ff.MediaInfo, stage: "_Stage") -> None:
        dst = self.scratch / "sprite.jpg"
        plan = ff.sprite_plan(info.duration_ms or 0, info.width or 16, info.height or 9)
        cmd = ff.sprite_command(
            self.s.ffmpeg_path,
            src,
            dst,
            plan,
            threads=self.s.media_ffmpeg_threads,
            demuxer=self.proxy_demuxer,
        )
        await self._run(cmd, self._timeout(info.duration_ms, 2, 300))
        await self._register("sprite", dst, "image/jpeg", dict(plan))
        await stage.fraction(1)

    async def waveform(self, src: Path, info: ff.MediaInfo, stage: "_Stage") -> None:
        plan = ff.waveform_plan(info.duration_ms or 0)
        cmd = ff.waveform_command(
            self.s.ffmpeg_path, src, plan, threads=self.s.media_ffmpeg_threads, demuxer=self.demuxer
        )
        result = await self._run(cmd, self._timeout(info.duration_ms, 2, 300), capture=True)
        peaks = ff.parse_waveform(result.stdout, plan["points"])
        if not peaks:
            raise ff.FfmpegFailed("la forma de onda no produjo datos")
        dst = self.scratch / "waveform.json"
        dst.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sample_rate": ff.WAVEFORM_RATE,
                    "duration_ms": info.duration_ms,
                    "peaks": peaks,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        await self._register("waveform", dst, "application/json", {"points": len(peaks)})
        await stage.fraction(1)

    async def audio16k(self, src: Path, info: ff.MediaInfo, stage: "_Stage") -> None:
        dst = self.scratch / "audio_16k.flac"
        cmd = ff.audio16k_command(
            self.s.ffmpeg_path, src, dst, threads=self.s.media_ffmpeg_threads, demuxer=self.demuxer
        )
        await self._run(cmd, self._timeout(info.duration_ms, 2, 300))
        await self._register(
            "audio_16k", dst, "audio/flac", {"sample_rate": 16000, "channels": 1, "codec": "flac"}
        )
        await stage.fraction(1)
