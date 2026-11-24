"""Casos de uso de clips. Única capa que toca estas tablas (CLAUDE.md regla 1).

Garantías:
- Un documento de edición inválido nunca se guarda: esquema + reglas semánticas + referencias reales
  (cada fuente es un asset de esta organización, del tipo correcto, listo y con duración suficiente).
- ``PATCH`` del documento exige la revisión esperada (concurrencia optimista): quien llega con una
  revisión vieja recibe 412 y no pisa el trabajo de otro.
- Las versiones son instantáneas inmutables (también lo impide un trigger en la base de datos).
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from klick.clips.builder import ASPECTS, DEFAULT_ASPECT, MIN_CLIP_MS, initial_document
from klick.clips.models import Clip, ClipVersion, EditProjectRow
from klick.core.audit import record_audit
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.outbox import emit
from klick.edit_project import EditProjectInvalid, parse_edit_project
from klick.edit_project.models import AudioItem, EditProject, VideoItem
from klick.edit_project.validate import Issue
from klick.media.service import UNSET, MediaService, Page, _like
from klick.orgs.principal import Principal

MAX_DOCUMENT_BYTES = 2_000_000
SOURCE_TOLERANCE_MS = 50  # los contenedores informan la duración con pequeño margen
EDITABLE_STATUSES = ("draft", "archived")


def _not_found(what: str = "Clip") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def _invalid(code: str, detail: str, status: int = 422) -> ApiError:
    return ApiError(status, code, "Invalid request", detail)


def _actor_type(principal: Principal) -> Literal["user", "api_token", "mcp"]:
    return {"api_token": "api_token", "mcp": "mcp"}.get(principal.origin, "user")  # type: ignore[return-value]


_IF_MATCH = re.compile(r'^(?:W/)?"?(\d{1,9})"?$')


def parse_if_match(if_match: str | None) -> int:
    """Lee `If-Match`. Sin cabecera: 428 (el cliente debe decir qué revisión editó)."""
    if if_match is None or not if_match.strip():
        raise ApiError(
            428,
            "if_match_required",
            "Precondition required",
            "Envía la cabecera If-Match con la revisión que editaste.",
        )
    match = _IF_MATCH.match(if_match.strip())
    if match is None:
        raise ApiError(400, "invalid_if_match", "Bad request", "If-Match debe ser la revisión.")
    return int(match.group(1))


def _issues_error(issues: list[Issue]) -> ApiError:
    return ApiError(
        422,
        "invalid_edit_project",
        "Invalid edit project",
        "El documento de edición no es válido.",
        errors=[{"code": i.code, "path": i.path, "message": i.message} for i in issues[:50]],
    )


@dataclass(frozen=True)
class ClipView:
    clip: Clip
    revision: int


class ClipService:
    def __init__(self, db: Database, media: MediaService) -> None:
        self._db = db
        self._media = media

    async def _record(
        self,
        s: AsyncSession,
        principal: Principal,
        action: str,
        resource_id: uuid.UUID,
        *,
        event: str | None = None,
        diff: dict[str, Any] | None = None,
    ) -> None:
        await record_audit(
            s,
            action=action,
            actor_type=_actor_type(principal),
            actor_id=principal.user_id,
            org_id=principal.org_id,
            resource_type="clip",
            resource_id=resource_id,
            diff=diff,
        )
        if event:
            await emit(
                s,
                type=event,
                org_id=principal.org_id,
                aggregate_type="clip",
                aggregate_id=resource_id,
                payload={"id": str(resource_id), **(diff or {})},
            )

    # ----------------------------------------------------------------------------- clips

    async def create_clip(
        self,
        principal: Principal,
        *,
        source_asset_id: uuid.UUID,
        start_ms: int,
        end_ms: int,
        title: str | None = None,
        project_id: uuid.UUID | None = None,
        aspect: str = DEFAULT_ASPECT,
    ) -> ClipView:
        """Crea un clip con su documento de edición inicial: un corte del video original."""
        if aspect not in ASPECTS:
            raise _invalid("invalid_aspect", f"Formato no permitido: {aspect}.")
        asset = (await self._media.get_asset(principal.org_id, source_asset_id)).asset  # 404
        if asset.kind != "video":
            raise _invalid("clip_source_not_video", "Solo se pueden crear clips desde un video.")
        if asset.status != "ready" or asset.duration_ms is None:
            raise ApiError(
                409, "asset_not_ready", "Conflict", "El video todavía se está procesando."
            )
        if not 0 <= start_ms < end_ms <= asset.duration_ms:
            raise _invalid("invalid_range", "El tramo debe estar dentro de la duración del video.")
        if end_ms - start_ms < MIN_CLIP_MS:
            raise _invalid("clip_too_short", f"El clip debe durar al menos {MIN_CLIP_MS} ms.")
        if project_id is not None:
            await self._media.get_project(principal.org_id, project_id)  # 404 si no es de la org
        document = initial_document(
            asset_id=str(asset.id), start_ms=start_ms, end_ms=end_ms, aspect=aspect
        )
        try:
            parse_edit_project(document)
        except EditProjectInvalid as exc:  # p. ej. un tramo de más de 4 h
            raise _issues_error(exc.issues) from None

        clip = Clip(
            id=new_id(),
            org_id=principal.org_id,
            project_id=project_id,
            source_asset_id=asset.id,
            title=(title or asset.title).strip()[:300] or asset.title,
            status="draft",
            created_by_type=_actor_type(principal),
            created_by=principal.user_id,
        )
        project = EditProjectRow(
            id=new_id(),
            org_id=principal.org_id,
            clip_id=clip.id,
            schema_version="1.0",
            document=document,
            revision=1,
            updated_by=principal.user_id,
        )
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            s.add(clip)
            await s.flush()
            s.add(project)
            await s.flush()
            await self._record(
                s,
                principal,
                "clip.created",
                clip.id,
                event="clip.created",
                diff={"source_asset_id": str(asset.id)},
            )
        return ClipView(clip, 1)

    async def _clip(
        self, s: AsyncSession, org_id: uuid.UUID, clip_id: uuid.UUID, *, lock: bool = False
    ) -> Clip:
        stmt = select(Clip).where(
            Clip.id == clip_id, Clip.org_id == org_id, Clip.deleted_at.is_(None)
        )
        clip = (await s.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if clip is None:
            raise _not_found()
        return clip

    async def get_clip(self, org_id: uuid.UUID, clip_id: uuid.UUID) -> ClipView:
        async with self._db.tenant_session(org_id) as s:
            clip = await self._clip(s, org_id, clip_id)
            revision = (
                await s.execute(
                    select(EditProjectRow.revision).where(EditProjectRow.clip_id == clip_id)
                )
            ).scalar_one()
            return ClipView(clip, revision)

    async def list_clips(
        self,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        cursor: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        source_asset_id: uuid.UUID | None = None,
        status: str | None = None,
        q: str | None = None,
    ) -> Page[Clip]:
        limit = max(1, min(limit, 200))
        async with self._db.tenant_session(org_id) as s:
            stmt = select(Clip).where(Clip.org_id == org_id, Clip.deleted_at.is_(None))
            if project_id:
                stmt = stmt.where(Clip.project_id == project_id)
            if source_asset_id:
                stmt = stmt.where(Clip.source_asset_id == source_asset_id)
            if status:
                stmt = stmt.where(Clip.status == status)
            if q and q.strip():
                term = q.strip()
                stmt = stmt.where(
                    or_(
                        Clip.tsv.op("@@")(func.websearch_to_tsquery("spanish", term)),
                        Clip.title.ilike(_like(term), escape="\\"),
                    )
                )
            if cursor:
                stmt = stmt.where(Clip.id < cursor)
            rows = list((await s.execute(stmt.order_by(Clip.id.desc()).limit(limit + 1))).scalars())
        return Page(rows[:limit], rows[limit - 1].id if len(rows) > limit else None)

    async def update_clip(
        self,
        principal: Principal,
        clip_id: uuid.UUID,
        *,
        title: str | None = None,
        status: str | None = None,
        project_id: Any = UNSET,
    ) -> ClipView:
        if status is not None and status not in EDITABLE_STATUSES:
            # `ready` lo fija el render al terminar; no se puede declarar a mano
            raise _invalid("status_not_allowed", "Solo se puede alternar entre draft y archived.")
        if project_id is not UNSET and project_id is not None:
            await self._media.get_project(principal.org_id, project_id)
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            clip = await self._clip(s, principal.org_id, clip_id, lock=True)
            changed: dict[str, Any] = {}
            if title is not None:
                clip.title = title.strip()
                changed["title"] = True
            if status is not None and status != clip.status:
                clip.status = status
                changed["status"] = status
            if project_id is not UNSET:
                clip.project_id = project_id
                changed["project"] = True
            await s.flush()
            await self._record(
                s, principal, "clip.updated", clip_id, event="clip.updated", diff=changed
            )
            revision = (
                await s.execute(
                    select(EditProjectRow.revision).where(EditProjectRow.clip_id == clip_id)
                )
            ).scalar_one()
            return ClipView(clip, revision)

    async def delete_clip(self, principal: Principal, clip_id: uuid.UUID) -> None:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            clip = await self._clip(s, principal.org_id, clip_id, lock=True)
            clip.deleted_at = datetime.now(UTC)
            await self._record(s, principal, "clip.deleted", clip_id, event="clip.deleted")

    async def count_live_clips_of_asset(self, org_id: uuid.UUID, asset_id: uuid.UUID) -> int:
        async with self._db.tenant_session(org_id) as s:
            return int(
                (
                    await s.execute(
                        select(func.count())
                        .select_from(Clip)
                        .where(
                            Clip.org_id == org_id,
                            Clip.source_asset_id == asset_id,
                            Clip.deleted_at.is_(None),
                        )
                    )
                ).scalar_one()
            )

    # ------------------------------------------------------------------ documento de edición

    async def get_edit_project(self, org_id: uuid.UUID, clip_id: uuid.UUID) -> EditProjectRow:
        async with self._db.tenant_session(org_id) as s:
            await self._clip(s, org_id, clip_id)
            return (
                await s.execute(
                    select(EditProjectRow).where(
                        EditProjectRow.clip_id == clip_id, EditProjectRow.org_id == org_id
                    )
                )
            ).scalar_one()

    async def reference_issues(self, org_id: uuid.UUID, doc: EditProject) -> list[Issue]:
        """Las fuentes deben ser assets reales de esta organización, del tipo y estado correctos."""
        ids = {uuid.UUID(s.asset_id) for s in doc.sources}
        assets = await self._media.assets_by_ids(org_id, ids)
        issues: list[Issue] = []
        by_source = {}
        for i, source in enumerate(doc.sources):
            asset = assets.get(uuid.UUID(source.asset_id))
            where = f"sources[{i}].asset_id"
            if asset is None:
                issues.append(
                    Issue("unknown_asset", where, "El archivo no existe en tu biblioteca.")
                )
            elif asset.kind != source.kind:
                issues.append(
                    Issue(
                        "asset_kind_mismatch",
                        where,
                        f"El archivo es {asset.kind} pero la fuente dice {source.kind}.",
                    )
                )
            elif asset.status != "ready":
                issues.append(Issue("asset_not_ready", where, "El archivo todavía no está listo."))
            else:
                by_source[source.id] = asset
        for ti, track in enumerate(doc.timeline.tracks):
            for ii, item in enumerate(track.items):
                if not isinstance(item, VideoItem | AudioItem):
                    continue
                asset = by_source.get(item.source_id)
                if (
                    asset is not None
                    and asset.duration_ms is not None
                    and item.source_out_ms > asset.duration_ms + SOURCE_TOLERANCE_MS
                ):
                    issues.append(
                        Issue(
                            "source_beyond_asset",
                            f"timeline.tracks[{ti}].items[{ii}].source_out_ms",
                            "El corte termina después del final del archivo original.",
                        )
                    )
        return issues

    async def update_edit_project(
        self,
        principal: Principal,
        clip_id: uuid.UUID,
        document: Any,
        if_match: str | None,
    ) -> EditProjectRow:
        """Guarda el documento si la revisión esperada es la vigente; si no, 412.

        Orden deliberado: primero que el clip exista (404 también para clips ajenos), después la
        cabecera y el documento. Así un clip de otra organización nunca revela nada por el tipo
        de error."""
        async with self._db.tenant_session(principal.org_id) as s:
            await self._clip(s, principal.org_id, clip_id)
        expected_revision = parse_if_match(if_match)
        try:
            size = len(json.dumps(document, separators=(",", ":")))
        except (TypeError, ValueError):
            raise _invalid("invalid_edit_project", "El documento no es JSON válido.") from None
        if size > MAX_DOCUMENT_BYTES:
            raise ApiError(
                413, "document_too_large", "Payload too large", "El documento es demasiado grande."
            )
        try:
            parsed = parse_edit_project(document)
        except EditProjectInvalid as exc:
            raise _issues_error(exc.issues) from None
        issues = await self.reference_issues(principal.org_id, parsed)
        if issues:
            raise _issues_error(issues)

        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await self._clip(s, principal.org_id, clip_id)
            row = (
                await s.execute(
                    update(EditProjectRow)
                    .where(
                        EditProjectRow.clip_id == clip_id,
                        EditProjectRow.org_id == principal.org_id,
                        EditProjectRow.revision == expected_revision,
                    )
                    .values(
                        document=document,
                        schema_version=parsed.schema_version,
                        revision=EditProjectRow.revision + 1,
                        updated_by=principal.user_id,
                    )
                    .returning(EditProjectRow)
                )
            ).scalar_one_or_none()
            if row is None:
                current = (
                    await s.execute(
                        select(EditProjectRow.revision).where(EditProjectRow.clip_id == clip_id)
                    )
                ).scalar_one()
                raise ApiError(
                    412,
                    "revision_mismatch",
                    "Precondition failed",
                    "Otra persona o pestaña guardó cambios antes que tú. Recarga el documento.",
                    headers={"ETag": f'"{current}"'},
                )
            return row

    # --------------------------------------------------------------------------- versiones

    async def create_version(
        self,
        principal: Principal,
        clip_id: uuid.UUID,
        *,
        label: str | None = None,
        reuse_if_unchanged: bool = False,
    ) -> ClipVersion:
        """Congela el documento actual en una versión inmutable y la deja como vigente."""
        if label is not None and len(label) > 200:
            raise _invalid("invalid_label", "La etiqueta admite hasta 200 caracteres.")
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            clip = await self._clip(s, principal.org_id, clip_id, lock=True)  # serializa versiones
            project = (
                await s.execute(
                    select(EditProjectRow)
                    .where(EditProjectRow.clip_id == clip_id)
                    .with_for_update()
                )
            ).scalar_one()
            last = (
                await s.execute(
                    select(ClipVersion)
                    .where(ClipVersion.clip_id == clip_id)
                    .order_by(ClipVersion.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if (
                reuse_if_unchanged
                and label is None
                and last is not None
                and last.document == project.document
            ):
                return last
            version = ClipVersion(
                id=new_id(),
                org_id=principal.org_id,
                clip_id=clip_id,
                version=(last.version + 1) if last else 1,
                schema_version=project.schema_version,
                document=project.document,
                label=label.strip() if label and label.strip() else None,
                created_by=principal.user_id,
            )
            s.add(version)
            await s.flush()
            clip.current_version_id = version.id
            await self._record(
                s,
                principal,
                "clip.version_created",
                clip_id,
                event="clip.version_created",
                diff={"version": version.version},
            )
            return version

    async def list_versions(
        self, org_id: uuid.UUID, clip_id: uuid.UUID, *, limit: int = 50, before: int | None = None
    ) -> tuple[list[ClipVersion], int | None]:
        limit = max(1, min(limit, 200))
        async with self._db.tenant_session(org_id) as s:
            await self._clip(s, org_id, clip_id)
            stmt = select(ClipVersion).where(
                ClipVersion.clip_id == clip_id, ClipVersion.org_id == org_id
            )
            if before is not None:
                stmt = stmt.where(ClipVersion.version < before)
            rows = list(
                (
                    await s.execute(stmt.order_by(ClipVersion.version.desc()).limit(limit + 1))
                ).scalars()
            )
        return rows[:limit], (rows[limit - 1].version if len(rows) > limit else None)

    async def get_version(self, org_id: uuid.UUID, clip_id: uuid.UUID, version: int) -> ClipVersion:
        async with self._db.tenant_session(org_id) as s:
            await self._clip(s, org_id, clip_id)
            row = (
                await s.execute(
                    select(ClipVersion).where(
                        ClipVersion.clip_id == clip_id,
                        ClipVersion.org_id == org_id,
                        ClipVersion.version == version,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise _not_found("Version")
            return row
