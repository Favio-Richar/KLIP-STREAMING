"""Ejecución segura de FFmpeg/FFprobe y validación del contenido (F1-04).

Todo archivo de usuario es dato no confiable (CLAUDE.md regla 6). Medidas, por capas:

1. **El demuxer lo elige Klick**, no el archivo: se fuerza con ``-f`` según el tipo declarado, así
   un archivo no puede activar demuxers peligrosos (HLS, ``concat``, etc.) aunque su contenido lo
   pida. Si el contenido no corresponde al formato declarado, se rechaza.
2. ``-protocol_whitelist file,pipe``: ninguna lista de reproducción puede abrir conexiones de red
   (SSRF). ``-nostdin``: nunca se lee la entrada estándar.
3. **Entorno limpio:** el proceso hijo no hereda las variables del worker (credenciales de base de
   datos, claves de cifrado, S3...).
4. **Límites:** tiempo máximo por proceso, salida acotada, cancelación cooperativa, y los límites de
   CPU/memoria/procesos del contenedor (ver ``infra/compose/workers.yml``).
5. Los mensajes de FFmpeg **no** se devuelven al usuario (podrían contener rutas o fragmentos del
   archivo): se registran truncados en el log del worker y al usuario le llega un código estable.
"""

import asyncio
import contextlib
import json
import math
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("klick.media.ffmpeg")

# Opciones comunes. `-protocol_whitelist` es una opción de ENTRADA: va antes de `-i`.
SAFE_ARGS = ["-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe"]

MAX_STDOUT_BYTES = 4 * 1024 * 1024
STDERR_TAIL_BYTES = 8 * 1024
MAX_LINE_BYTES = 8 * 1024
CANCEL_POLL_S = 2.0

# Demuxer forzado por tipo declarado (ver nota 1 del módulo).
_MP4 = "mov,mp4,m4a,3gp,3g2,mj2"
_MKV = "matroska,webm"
DEMUXERS: dict[str, str] = {
    "video/mp4": _MP4,
    "video/quicktime": _MP4,
    "video/3gpp": _MP4,
    "audio/mp4": _MP4,
    "video/x-matroska": _MKV,
    "video/webm": _MKV,
    "audio/webm": _MKV,
    "video/x-msvideo": "avi",
    "video/mpeg": "mpeg",
    "video/mp2t": "mpegts",
    "audio/mpeg": "mp3",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/ogg": "ogg",
    "image/png": "image2",
    "image/jpeg": "image2",
    "image/webp": "image2",
    "image/gif": "gif",
}


class MediaRejected(Exception):
    """El contenido no es válido o excede los límites. Es permanente: reintentar no lo arregla."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class FfmpegFailed(Exception):
    """FFmpeg terminó con error sobre un archivo ya validado (puede ser transitorio)."""


class MediaCanceled(Exception):
    """El job se canceló mientras corría un proceso."""


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr_tail: str


def clean_env() -> dict[str, str]:
    """Entorno mínimo para los procesos hijos: solo lo necesario para localizar el binario."""
    return {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


async def run(
    cmd: Sequence[str],
    *,
    timeout_s: float,
    capture_stdout: bool = False,
    on_progress_line: Callable[[str], Awaitable[None]] | None = None,
    is_canceled: Callable[[], Awaitable[bool]] | None = None,
) -> RunResult:
    """Ejecuta un proceso sin shell, sin stdin y con entorno limpio.

    ``capture_stdout`` recoge la salida (acotada a 4 MiB: más se trata como ataque y se mata el
    proceso). ``on_progress_line`` recibe cada línea de stdout (formato ``-progress pipe:1``).
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=clean_env(),
    )
    out_chunks: list[bytes] = []
    out_size = 0
    err_tail = b""
    overflow = False

    async def read_stdout() -> None:
        """Lee por bloques (nunca por líneas: una línea gigante no debe romper el lector)."""
        nonlocal out_size, overflow
        assert proc.stdout is not None  # noqa: S101 - PIPE garantiza el flujo
        pending = b""
        while chunk := await proc.stdout.read(65536):
            if capture_stdout:
                out_size += len(chunk)
                if out_size > MAX_STDOUT_BYTES:
                    overflow = True
                    proc.kill()
                    return
                out_chunks.append(chunk)
            if on_progress_line is not None:
                pending += chunk
                *lines, pending = pending.split(b"\n")
                pending = pending[-MAX_LINE_BYTES:]  # una línea sin fin no crece sin límite
                for line in lines:
                    await on_progress_line(
                        line[:MAX_LINE_BYTES].decode("utf-8", "replace").rstrip()
                    )

    async def read_stderr() -> None:
        nonlocal err_tail
        assert proc.stderr is not None  # noqa: S101
        while chunk := await proc.stderr.read(4096):
            err_tail = (err_tail + chunk)[-STDERR_TAIL_BYTES:]

    readers = [asyncio.create_task(read_stdout()), asyncio.create_task(read_stderr())]
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=CANCEL_POLL_S)
                break
            except TimeoutError:
                pass
            if time.monotonic() >= deadline:
                proc.kill()
                await proc.wait()
                raise FfmpegFailed(f"tiempo máximo excedido ({timeout_s:.0f}s)")
            if is_canceled is not None and await is_canceled():
                proc.kill()
                await proc.wait()
                raise MediaCanceled()
        await asyncio.gather(*readers)
    finally:
        for task in readers:
            if not task.done():
                task.cancel()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    if overflow:
        raise MediaRejected("invalid_media", "salida de análisis desmesurada")
    return RunResult(
        proc.returncode if proc.returncode is not None else -1,
        b"".join(out_chunks).decode("utf-8", "replace"),
        err_tail.decode("utf-8", "replace"),
    )


# ------------------------------------------------------------------------------ análisis


@dataclass(frozen=True)
class Limits:
    max_duration_s: int
    max_dimension: int
    max_streams: int


@dataclass(frozen=True)
class MediaInfo:
    kind: str
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    container: str | None
    bitrate_kbps: int | None
    has_audio: bool
    has_video: bool
    probe: dict[str, Any]


async def ffprobe(ffprobe_path: str, path: Path, mime: str, *, timeout_s: float) -> dict[str, Any]:
    """Analiza el archivo con el demuxer correspondiente al tipo declarado."""
    demuxer = DEMUXERS.get(mime)
    if demuxer is None:
        raise MediaRejected("unsupported_media_type")
    cmd = [
        ffprobe_path,
        *SAFE_ARGS,
        "-f",
        demuxer,
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        str(path),
    ]
    try:
        result = await run(cmd, timeout_s=timeout_s, capture_stdout=True)
    except FfmpegFailed:
        raise MediaRejected("probe_timeout") from None
    if result.returncode != 0:
        log.info("ffprobe_rejected", stderr=result.stderr_tail[-400:])
        raise MediaRejected("invalid_media")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise MediaRejected("invalid_media") from None
    if not isinstance(data, dict):
        raise MediaRejected("invalid_media")
    return data


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _int(value: Any) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


def _fraction(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return _num(value)
    num, _, den = value.partition("/")
    n, d = _num(num), _num(den)
    if n is None or d is None or d == 0:
        return None
    return n / d


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and "rotation" in side:
            return int(_num(side["rotation"]) or 0) % 360
    return int(_num((stream.get("tags") or {}).get("rotate")) or 0) % 360


def _curated(fmt: dict[str, Any], streams: list[dict[str, Any]]) -> dict[str, Any]:
    """Solo campos técnicos y acotados: nunca etiquetas libres (título, comentarios) del archivo."""

    def s(value: Any, limit: int = 64) -> str | None:
        return str(value)[:limit] if value is not None else None

    return {
        "format": {
            "format_name": s(fmt.get("format_name"), 100),
            "duration": _num(fmt.get("duration")),
            "size": _int(fmt.get("size")),
            "bit_rate": _int(fmt.get("bit_rate")),
        },
        "streams": [
            {
                "index": _int(st.get("index")),
                "codec_type": s(st.get("codec_type")),
                "codec_name": s(st.get("codec_name")),
                "width": _int(st.get("width")),
                "height": _int(st.get("height")),
                "avg_frame_rate": s(st.get("avg_frame_rate")),
                "pix_fmt": s(st.get("pix_fmt")),
                "sample_rate": _int(st.get("sample_rate")),
                "channels": _int(st.get("channels")),
            }
            for st in streams
        ],
    }


def analyze(data: dict[str, Any], expected_kind: str, limits: Limits) -> MediaInfo:
    """Valida el resultado de FFprobe contra el tipo esperado y los límites. Lanza
    :class:`MediaRejected` con un código estable si el contenido no es aceptable."""
    raw_streams = data.get("streams")
    fmt = data.get("format")
    if not isinstance(raw_streams, list) or not isinstance(fmt, dict) or not raw_streams:
        raise MediaRejected("invalid_media")
    streams = [s for s in raw_streams if isinstance(s, dict)]
    if len(streams) > limits.max_streams:
        raise MediaRejected("too_many_streams")

    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if expected_kind in ("video", "image") and not videos:
        raise MediaRejected("no_video_stream")
    if expected_kind == "audio" and not audios:
        raise MediaRejected("no_audio_stream")

    width = height = None
    fps = None
    if videos and expected_kind != "audio":
        v = videos[0]
        width, height = _int(v.get("width")), _int(v.get("height"))
        if not width or not height or width <= 0 or height <= 0:
            raise MediaRejected("invalid_media")
        if _rotation(v) in (90, 270):
            width, height = height, width
        if max(width, height) > limits.max_dimension:
            raise MediaRejected("too_large_dimensions")
        if expected_kind == "video":
            fps = _fraction(v.get("avg_frame_rate")) or _fraction(v.get("r_frame_rate"))
            if fps is None or not 0 < fps <= 240:
                raise MediaRejected("invalid_frame_rate")

    duration_ms: int | None = None
    if expected_kind in ("video", "audio"):
        duration = _num(fmt.get("duration"))
        if not duration:
            main = (videos or audios)[0]
            duration = _num(main.get("duration"))
        if not duration or duration <= 0:
            raise MediaRejected("unknown_duration")
        if duration > limits.max_duration_s:
            raise MediaRejected("too_long")
        duration_ms = round(duration * 1000)

    bitrate = _int(fmt.get("bit_rate"))
    size = _int(fmt.get("size"))
    if bitrate is None and size and duration_ms:
        bitrate = round(size * 8 / (duration_ms / 1000))
    return MediaInfo(
        kind=expected_kind,
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=round(fps, 3) if fps else None,
        video_codec=str(videos[0].get("codec_name"))[:64]
        if videos and expected_kind != "audio"
        else None,
        audio_codec=str(audios[0].get("codec_name"))[:64] if audios else None,
        container=str(fmt.get("format_name") or "")[:100] or None,
        bitrate_kbps=round(bitrate / 1000) if bitrate else None,
        has_audio=bool(audios) and expected_kind != "image",
        has_video=bool(videos),
        probe=_curated(fmt, streams),
    )


# ------------------------------------------------------------------------------- derivados


def _input(src: Path, demuxer: str | None) -> list[str]:
    """Entrada con el demuxer forzado (el mismo que validó FFprobe): FFmpeg no vuelve a adivinar."""
    return [*(["-f", demuxer] if demuxer else []), "-i", str(src)]


def _base(ffmpeg: str, src: Path, threads: int, demuxer: str | None = None) -> list[str]:
    return [ffmpeg, "-y", "-nostdin", *SAFE_ARGS, "-threads", str(threads), *_input(src, demuxer)]


def proxy_command(
    ffmpeg: str, src: Path, dst: Path, *, threads: int, demuxer: str | None = None
) -> list[str]:
    """Proxy H.264 a 720p máximo (nunca amplía), GOP corto para edición ágil, audio opcional."""
    return [
        *_base(ffmpeg, src, threads, demuxer),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-vf", "scale=-2:min(720\\,ih),format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(dst),
    ]  # fmt: skip


def thumbnail_command(
    ffmpeg: str, src: Path, dst: Path, *, at_s: float, threads: int, demuxer: str | None = None
) -> list[str]:
    return [
        ffmpeg, "-y", "-nostdin", *SAFE_ARGS, "-threads", str(threads),
        "-ss", f"{max(at_s, 0):.3f}", *_input(src, demuxer),
        "-frames:v", "1",
        "-vf", "scale=min(640\\,iw):-2",
        "-q:v", "3",
        str(dst),
    ]  # fmt: skip


SPRITE_MAX_FRAMES = 100
SPRITE_THUMB_WIDTH = 160


def sprite_plan(duration_ms: int, width: int, height: int) -> dict[str, int]:
    """Cuántas miniaturas y cada cuánto: como máximo 100, una cada ``interval`` segundos."""
    duration_s = max(duration_ms / 1000, 0.001)
    interval_s = max(1, math.ceil(duration_s / SPRITE_MAX_FRAMES))
    count = max(1, math.ceil(duration_s / interval_s))
    thumb_h = max(2, round(SPRITE_THUMB_WIDTH * height / width / 2) * 2)
    return {
        "interval_ms": interval_s * 1000,
        "count": count,
        "cols": count,
        "rows": 1,
        "thumb_width": SPRITE_THUMB_WIDTH,
        "thumb_height": thumb_h,
    }


def sprite_command(
    ffmpeg: str,
    src: Path,
    dst: Path,
    plan: dict[str, int],
    *,
    threads: int,
    demuxer: str | None = None,
) -> list[str]:
    interval_s = plan["interval_ms"] // 1000
    return [
        *_base(ffmpeg, src, threads, demuxer),
        "-an",
        "-vf",
        f"fps=1/{interval_s},scale={plan['thumb_width']}:{plan['thumb_height']},tile={plan['count']}x1",
        "-frames:v", "1", "-q:v", "5",
        str(dst),
    ]  # fmt: skip


WAVEFORM_RATE = 8000
WAVEFORM_MAX_POINTS = 2000


def waveform_plan(duration_ms: int) -> dict[str, int]:
    duration_s = max(duration_ms / 1000, 0.001)
    points = int(min(WAVEFORM_MAX_POINTS, max(50, duration_s * 20)))
    return {
        "points": points,
        "samples_per_point": max(1, round(WAVEFORM_RATE * duration_s / points)),
    }


def waveform_command(
    ffmpeg: str, src: Path, plan: dict[str, int], *, threads: int, demuxer: str | None = None
) -> list[str]:
    """Pico por tramo medido por FFmpeg (``astats``): sin decodificar el audio en Python."""
    return [
        *_base(ffmpeg, src, threads, demuxer),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(WAVEFORM_RATE),
        "-af",
        f"asetnsamples=n={plan['samples_per_point']}:p=0,astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.Peak_level:file=-",
        "-f", "null", "-",
    ]  # fmt: skip


def parse_waveform(output: str, points: int) -> list[float]:
    """Convierte las líneas ``Peak_level=<dB>`` a amplitudes 0..1 (3 decimales)."""
    peaks: list[float] = []
    for line in output.splitlines():
        if "Peak_level=" not in line:
            continue
        raw = line.split("=", 1)[1].strip()
        db = _num(raw)  # "-inf" → None
        peaks.append(0.0 if db is None else round(min(1.0, 10 ** (db / 20)), 3))
    if len(peaks) > points:
        peaks = peaks[:points]
    return peaks


def audio16k_command(
    ffmpeg: str, src: Path, dst: Path, *, threads: int, demuxer: str | None = None
) -> list[str]:
    """Mono 16 kHz en FLAC (sin pérdida y la mitad de grande que WAV): entrada de la transcripción."""
    return [
        *_base(ffmpeg, src, threads, demuxer),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac",
        str(dst),
    ]  # fmt: skip


def progress_fraction(line: str, duration_s: float) -> float | None:
    """De una línea de ``-progress`` (``out_time_us=...``) a 0..1."""
    if not line.startswith("out_time_us=") or duration_s <= 0:
        return None
    value = _num(line.split("=", 1)[1])
    if value is None or value < 0:
        return None
    return min(1.0, value / 1_000_000 / duration_s)
