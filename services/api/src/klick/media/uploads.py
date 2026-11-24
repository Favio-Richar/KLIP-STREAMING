"""Subida multipart directa al almacén (docs/03 §4.1, docs/07 §1).

Flujo: ``start`` reserva asset + objeto + subida multipart → el navegador sube las partes con URLs
firmadas (``part_urls``) → ``complete`` ensambla, verifica el tamaño real y deja el asset en
``processing``. ``state`` permite reanudar tras un corte; ``abort`` y el recolector limpian lo
abandonado.

Seguridad:
- El servidor nunca confía en lo que el cliente declare haber subido: al completar lista las
  partes en el almacén y compara el tamaño final con el declarado.
- El ``sha256`` declarado solo sirve para deduplicar contra objetos cuyo hash ya fue verificado
  por el servidor (lo calcula el análisis, F1-04); nunca se guarda tal cual.
- Sin ``rights_confirmed`` no se crea nada (restricción también en la base de datos).
"""

import math
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath

import structlog
from sqlalchemy import delete, select

from klick.core.audit import record_audit
from klick.core.config import Settings
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.outbox import emit
from klick.media.models import MediaAsset, Upload
from klick.media.service import AssetView, MediaService, _actor_type
from klick.orgs.principal import Principal
from klick.storage.backend import MAX_PARTS, MIN_PART_SIZE, CompletedPart, StorageBackend
from klick.storage.keys import asset_original
from klick.storage.models import StorageObject
from klick.storage.service import StorageService

log = structlog.get_logger("klick.media.uploads")

MAX_OPEN_UPLOADS_PER_ORG = 20
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")

# Tipos aceptados al declarar la subida. La validación real es por contenido (media.probe, F1-04).
_ALLOWED_TYPES: dict[str, str] = {
    "video/mp4": "video",
    "video/quicktime": "video",
    "video/x-matroska": "video",
    "video/webm": "video",
    "video/x-msvideo": "video",
    "video/mpeg": "video",
    "video/mp2t": "video",
    "video/3gpp": "video",
    "audio/mpeg": "audio",
    "audio/mp4": "audio",
    "audio/aac": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/flac": "audio",
    "audio/ogg": "audio",
    "audio/webm": "audio",
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
}
# Los navegadores a veces no informan el tipo (p. ej. .mkv): se infiere por extensión.
_EXT_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".ts": "video/mp2t",
    ".3gp": "video/3gpp",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _invalid(code: str, detail: str, status: int = 422) -> ApiError:
    return ApiError(status, code, "Invalid request", detail)


def clean_filename(raw: str) -> str:
    """Nombre seguro para mostrar y para ``Content-Disposition``: sin rutas ni controles."""
    name = PureWindowsPath(PurePosixPath(raw).name).name
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*\\/').strip(" .")
    return name[:200]


def detect_type(filename: str, declared: str | None) -> tuple[str, str]:
    """Devuelve (mime, kind) o rechaza con 415."""
    mime = (declared or "").split(";")[0].strip().lower()
    if mime not in _ALLOWED_TYPES:
        suffix = PurePosixPath(filename).suffix.lower()
        mime = _EXT_TYPES.get(suffix, "")
    if mime not in _ALLOWED_TYPES:
        raise _invalid(
            "unsupported_media_type",
            "Formato no admitido. Sube un video, audio o imagen común (mp4, mov, mkv, webm, mp3, wav, png, jpg...).",
            415,
        )
    return mime, _ALLOWED_TYPES[mime]


def choose_part_size(size_bytes: int, minimum: int) -> int:
    """Partes de al menos ``minimum`` y de forma que nunca superen MAX_PARTS (múltiplos de 1 MiB)."""
    minimum = max(minimum, MIN_PART_SIZE)
    needed = math.ceil(size_bytes / (MAX_PARTS - 1))
    mib = 1024 * 1024
    return max(minimum, math.ceil(needed / mib) * mib)


@dataclass(frozen=True)
class StartResult:
    upload: Upload | None
    asset: MediaAsset
    deduplicated: bool
    total_parts: int


@dataclass(frozen=True)
class UploadState:
    upload: Upload
    total_parts: int
    uploaded_parts: list[int]


class UploadService:
    def __init__(
        self,
        db: Database,
        backend: StorageBackend,
        storage: StorageService,
        media: MediaService,
        settings: Settings,
    ) -> None:
        self._db = db
        self._backend = backend
        self._storage = storage
        self._media = media
        self._s = settings

    # ------------------------------------------------------------------------------ inicio

    async def start(
        self,
        principal: Principal,
        *,
        filename: str,
        size_bytes: int,
        content_type: str | None,
        rights_confirmed: bool,
        title: str | None = None,
        project_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
        sha256: str | None = None,
    ) -> StartResult:
        if not rights_confirmed:
            raise _invalid(
                "rights_not_confirmed",
                "Debes confirmar que tienes derecho a usar este contenido.",
            )
        if size_bytes < 1:
            raise _invalid("invalid_size", "El archivo está vacío.")
        if size_bytes > self._s.upload_max_bytes:
            raise _invalid(
                "upload_too_large",
                f"El archivo supera el máximo de {self._s.upload_max_bytes // 1024**3} GB.",
                413,
            )
        name = clean_filename(filename)
        if not name:
            raise _invalid("invalid_filename", "El nombre del archivo no es válido.")
        mime, kind = detect_type(name, content_type)
        asset_title = (title or PurePosixPath(name).stem or name).strip()[:300] or name
        if sha256 is not None and not _SHA256.match(sha256):
            raise _invalid("invalid_sha256", "El sha256 debe ser hexadecimal de 64 caracteres.")

        duplicate = await self._dedupe(
            principal,
            sha256,
            title=asset_title,
            filename=name,
            project_id=project_id,
            folder_id=folder_id,
        )
        if duplicate is not None:
            return StartResult(None, duplicate, True, 0)

        if await self._media.count_open_uploads(principal.org_id) >= MAX_OPEN_UPLOADS_PER_ORG:
            raise ApiError(
                429,
                "too_many_uploads",
                "Too many uploads",
                "Hay demasiadas subidas en curso. Termina o cancela alguna antes de empezar otra.",
                headers={"Retry-After": "60"},
            )

        asset_id = new_id()
        key = asset_original(principal.org_id, asset_id)
        part_size = choose_part_size(size_bytes, self._s.upload_part_min_bytes)
        s3_upload_id = await self._backend.create_multipart(key, mime)
        try:
            obj = await self._storage.register_upload(
                principal.org_id, key=key, mime=mime, created_by=principal.user_id
            )
            asset = await self._media.create_asset(
                principal,
                kind=kind,
                title=asset_title,
                storage_object_id=obj.id,
                original_filename=name,
                project_id=project_id,
                folder_id=folder_id,
                rights_confirmed=True,
                asset_id=asset_id,
            )
            upload = await self._media.create_upload(
                principal,
                asset_id=asset.id,
                storage_object_id=obj.id,
                s3_upload_id=s3_upload_id,
                part_size=part_size,
                size_bytes=size_bytes,
                expires_at=datetime.now(UTC) + timedelta(hours=self._s.storage_upload_stale_h),
            )
        except BaseException:
            await self._backend.abort_multipart(key, s3_upload_id)
            raise
        return StartResult(upload, asset, False, math.ceil(size_bytes / part_size))

    async def _dedupe(
        self,
        principal: Principal,
        sha256: str | None,
        *,
        title: str,
        filename: str,
        project_id: uuid.UUID | None,
        folder_id: uuid.UUID | None,
    ) -> MediaAsset | None:
        """Si la organización ya tiene ese contenido (hash verificado), crea un asset nuevo que
        comparte el objeto y hereda los metadatos técnicos: no se sube nada."""
        if sha256 is None:
            return None
        obj = await self._storage.find_ready_by_sha256(principal.org_id, bytes.fromhex(sha256))
        if obj is None:
            return None
        original = await self._media.find_ready_asset_by_object(principal.org_id, obj.id)
        if original is None:
            return None
        asset = await self._media.create_asset(
            principal,
            kind=original.kind,
            title=title,
            storage_object_id=obj.id,
            original_filename=filename,
            project_id=project_id,
            folder_id=folder_id,
            rights_confirmed=True,
        )
        await self._media.transition_asset(principal.org_id, asset.id, "processing")
        ready = await self._media.transition_asset(
            principal.org_id,
            asset.id,
            "ready",
            probe=original.probe,
            duration_ms=original.duration_ms,
            width=original.width,
            height=original.height,
            fps=original.fps,
            video_codec=original.video_codec,
            audio_codec=original.audio_codec,
            container=original.container,
            bitrate_kbps=original.bitrate_kbps,
            has_audio=original.has_audio,
        )
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await record_audit(
                s,
                action="upload.deduplicated",
                actor_type=_actor_type(principal),
                actor_id=principal.user_id,
                org_id=principal.org_id,
                resource_type="asset",
                resource_id=ready.id,
                diff={"source_asset_id": str(original.id)},
            )
        return ready

    # ------------------------------------------------------------------------ estado y URLs

    @staticmethod
    def total_parts(upload: Upload) -> int:
        if not upload.size_bytes or not upload.part_size:  # siempre presentes al iniciar
            raise ApiError(409, "upload_not_multipart", "Conflict", "La subida no es multipart.")
        return math.ceil(upload.size_bytes / upload.part_size)

    async def _open_upload(self, org_id: uuid.UUID, upload_id: uuid.UUID) -> tuple[Upload, str]:
        upload = await self._media.get_upload(org_id, upload_id)
        obj = await self._storage.get(org_id, upload.storage_object_id)
        return upload, obj.key

    async def state(self, org_id: uuid.UUID, upload_id: uuid.UUID) -> UploadState:
        upload, key = await self._open_upload(org_id, upload_id)
        uploaded: list[int] = []
        if upload.status == "open" and upload.s3_upload_id:
            parts = await self._backend.list_parts(key, upload.s3_upload_id)
            uploaded = sorted(p.part_number for p in parts)
        elif upload.status == "completed":
            uploaded = list(range(1, self.total_parts(upload) + 1))
        return UploadState(upload, self.total_parts(upload), uploaded)

    async def part_urls(
        self, org_id: uuid.UUID, upload_id: uuid.UUID, part_numbers: list[int]
    ) -> tuple[dict[int, str], int]:
        upload, key = await self._open_upload(org_id, upload_id)
        if upload.status != "open" or not upload.s3_upload_id:
            raise ApiError(409, "upload_not_open", "Conflict", "La subida ya no está abierta.")
        remaining = int((upload.expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise ApiError(409, "upload_expired", "Conflict", "La subida expiró. Empieza otra.")
        total = self.total_parts(upload)
        numbers = sorted(set(part_numbers))
        if not numbers or len(numbers) > self._s.upload_urls_per_request:
            raise _invalid(
                "invalid_parts",
                f"Pide entre 1 y {self._s.upload_urls_per_request} partes por solicitud.",
            )
        if numbers[0] < 1 or numbers[-1] > total:
            raise _invalid("invalid_parts", f"Las partes válidas van de 1 a {total}.")
        ttl = max(1, min(self._s.storage_url_ttl_s, remaining))
        urls = {
            n: self._backend.presign_part(key, upload.s3_upload_id, n, expires_s=ttl)
            for n in numbers
        }
        return urls, ttl

    # ----------------------------------------------------------------------------- completar

    async def complete(self, principal: Principal, upload_id: uuid.UUID) -> AssetView:
        org_id = principal.org_id
        upload, key = await self._open_upload(org_id, upload_id)
        if upload.status == "completed":  # reintento del cliente: misma respuesta
            return await self._media.get_asset(org_id, upload.asset_id)
        if upload.status not in ("open", "completing"):
            raise ApiError(409, "upload_not_open", "Conflict", f"La subida está {upload.status}.")
        if not upload.s3_upload_id or not upload.size_bytes:
            raise ApiError(409, "upload_not_multipart", "Conflict", "La subida no es multipart.")
        if upload.status == "open":
            await self._media.transition_upload(org_id, upload_id, "completing")

        # ¿Ya se ensambló en un intento anterior que cayó antes de confirmar?
        info = await self._backend.head(key)
        if info is None:
            parts = await self._backend.list_parts(key, upload.s3_upload_id)
            have = {p.part_number for p in parts}
            missing = [n for n in range(1, self.total_parts(upload) + 1) if n not in have]
            if missing:
                await self._media.transition_upload(org_id, upload_id, "open")
                raise ApiError(
                    409,
                    "upload_incomplete",
                    "Conflict",
                    f"Faltan {len(missing)} partes por subir.",
                    errors=[{"missing_parts": missing[:200]}],
                )
            await self._backend.complete_multipart(
                key, upload.s3_upload_id, [CompletedPart(p.part_number, p.etag) for p in parts]
            )
            info = await self._backend.head(key)
        if info is None or info.size != upload.size_bytes:
            await self._reject_mismatch(principal, upload, key, info.size if info else None)
            raise _invalid(
                "upload_size_mismatch",
                "El tamaño del archivo subido no coincide con el declarado. Vuelve a intentarlo.",
            )

        await self._storage.mark_ready(org_id, upload.storage_object_id)
        current = (await self._media.get_asset(org_id, upload.asset_id)).asset
        if current.status == "uploading":  # un reintento tras una caída ya lo movió
            await self._media.transition_asset(org_id, upload.asset_id, "processing")
        await self._media.transition_upload(org_id, upload_id, "completed")
        async with self._db.tenant_session(org_id, principal.user_id) as s:
            await record_audit(
                s,
                action="upload.completed",
                actor_type=_actor_type(principal),
                actor_id=principal.user_id,
                org_id=org_id,
                resource_type="asset",
                resource_id=upload.asset_id,
                diff={"size_bytes": upload.size_bytes},
            )
            await emit(
                s,
                type="asset.uploaded",
                org_id=org_id,
                aggregate_type="asset",
                aggregate_id=upload.asset_id,
                payload={"id": str(upload.asset_id), "status": "processing"},
            )
        # el análisis (F1-04): idempotente por asset, así que un reintento de `complete` no duplica
        await self._media.enqueue_probe(
            org_id,
            upload.asset_id,
            round_key="upload",
            created_by=principal.user_id,
            created_by_type=_actor_type(principal),
        )
        return await self._media.get_asset(org_id, upload.asset_id)

    async def _reject_mismatch(
        self, principal: Principal, upload: Upload, key: str, actual: int | None
    ) -> None:
        """Tamaño distinto: se borra lo subido y se descarta el asset incompleto."""
        await self._backend.delete(key)
        await self._media.transition_upload(principal.org_id, upload.id, "aborted")
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await record_audit(
                s,
                action="upload.rejected",
                actor_type=_actor_type(principal),
                actor_id=principal.user_id,
                org_id=principal.org_id,
                resource_type="asset",
                resource_id=upload.asset_id,
                diff={"declared": upload.size_bytes, "actual": actual},
            )
        await self._media.discard_unfinished_asset(principal.org_id, upload.asset_id)

    # ------------------------------------------------------------------------------ abortar

    async def abort(self, principal: Principal, upload_id: uuid.UUID) -> None:
        upload, key = await self._open_upload(principal.org_id, upload_id)
        if upload.status == "completed":
            raise ApiError(
                409, "upload_not_open", "Conflict", "La subida ya terminó; elimina el asset."
            )
        if upload.s3_upload_id:
            await self._backend.abort_multipart(key, upload.s3_upload_id)
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await record_audit(
                s,
                action="upload.aborted",
                actor_type=_actor_type(principal),
                actor_id=principal.user_id,
                org_id=principal.org_id,
                resource_type="asset",
                resource_id=upload.asset_id,
            )
        await self._media.discard_unfinished_asset(principal.org_id, upload.asset_id)


async def reap_expired_uploads(db: Database, backend: StorageBackend, batch: int = 100) -> int:
    """Tarea del scheduler (rol de sistema, todas las organizaciones): aborta las subidas abiertas
    vencidas, libera su multipart en el almacén y borra el asset que nunca llegó a existir."""
    async with db.system_session() as s:
        rows = (
            await s.execute(
                select(
                    Upload.id,
                    Upload.org_id,
                    Upload.asset_id,
                    Upload.s3_upload_id,
                    StorageObject.key,
                )
                .join(StorageObject, StorageObject.id == Upload.storage_object_id)
                .where(
                    Upload.status.in_(("open", "completing")), Upload.expires_at < datetime.now(UTC)
                )
                .order_by(Upload.expires_at)
                .limit(batch)
                .with_for_update(of=Upload, skip_locked=True)
            )
        ).all()
    reaped = 0
    for upload_id, org_id, asset_id, s3_upload_id, key in rows:
        try:
            if s3_upload_id:
                await backend.abort_multipart(key, s3_upload_id)
        except Exception:
            log.exception("upload_abort_failed", upload_id=str(upload_id))
            continue
        async with db.system_session() as s:
            await s.execute(
                delete(MediaAsset).where(
                    MediaAsset.id == asset_id,
                    MediaAsset.org_id == org_id,
                    MediaAsset.status == "uploading",
                )
            )
        reaped += 1
    if reaped:
        log.info("uploads_reaped", count=reaped)
    return reaped
