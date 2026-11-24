"""Casos de uso de la biblioteca de medios. Única capa que toca estas tablas (CLAUDE.md regla 1).

Autorización: el router exige el permiso; aquí se garantiza el aislamiento (RLS + filtros por
``org_id``) y las reglas de negocio. Toda mutación del usuario deja auditoría y un evento.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from klick.core.audit import record_audit
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.outbox import emit
from klick.media.models import (
    ASSET_KINDS,
    DERIVATIVE_KINDS,
    SOURCE_TYPES,
    AssetTag,
    Folder,
    MediaAsset,
    MediaDerivative,
    Project,
    Tag,
    Upload,
)
from klick.orgs.principal import Principal
from klick.storage.service import StorageService

MAX_FOLDER_DEPTH = 10
UNSET: Any = object()  # distingue "no enviado" de "null" en actualizaciones parciales

# Transiciones válidas del estado de un asset (el job `media.probe` las recorre).
ASSET_TRANSITIONS: dict[str, frozenset[str]] = {
    "uploading": frozenset({"processing", "failed"}),
    "processing": frozenset({"ready", "failed"}),
    "ready": frozenset({"processing"}),  # volver a analizar
    "failed": frozenset({"processing"}),
}
UPLOAD_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"completing", "aborted", "expired"}),
    "completing": frozenset({"completed", "open", "aborted"}),
    "completed": frozenset(),
    "aborted": frozenset(),
    "expired": frozenset(),
}


def _not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def _conflict(code: str, detail: str) -> ApiError:
    return ApiError(409, code, "Conflict", detail)


def _invalid(code: str, detail: str) -> ApiError:
    return ApiError(422, code, "Invalid request", detail)


def _constraint(exc: IntegrityError) -> str:
    orig = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
    return ""


def _actor_type(principal: Principal) -> Literal["user", "api_token", "mcp"]:
    return {"api_token": "api_token", "mcp": "mcp"}.get(principal.origin, "user")  # type: ignore[return-value]


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


@dataclass(frozen=True)
class AssetView:
    asset: MediaAsset
    tags: list[Tag] = field(default_factory=list)


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    next_cursor: uuid.UUID | None


class MediaService:
    def __init__(self, db: Database, storage: StorageService) -> None:
        self._db = db
        self._storage = storage

    # --------------------------------------------------------------------------- auditoría

    async def _record(
        self,
        s: AsyncSession,
        principal: Principal,
        action: str,
        resource_type: str,
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
            resource_type=resource_type,
            resource_id=resource_id,
            diff=diff,
        )
        if event:
            await emit(
                s,
                type=event,
                org_id=principal.org_id,
                aggregate_type=resource_type,
                aggregate_id=resource_id,
                payload={"id": str(resource_id), **(diff or {})},
            )

    # ----------------------------------------------------------------------------- proyectos

    async def create_project(
        self,
        principal: Principal,
        *,
        name: str,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> Project:
        project = Project(
            id=new_id(),
            org_id=principal.org_id,
            name=name.strip(),
            description=description,
            settings=settings or {},
            created_by=principal.user_id,
        )
        try:
            async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
                s.add(project)
                await s.flush()
                await self._record(
                    s, principal, "project.created", "project", project.id, event="project.created"
                )
        except IntegrityError as exc:
            raise self._project_conflict(exc) from None
        return project

    @staticmethod
    def _project_conflict(exc: IntegrityError) -> ApiError:
        if _constraint(exc) == "uq_projects__org_id_name":
            return _conflict("project_name_taken", "Ya existe un proyecto con ese nombre.")
        raise exc

    async def list_projects(
        self,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        cursor: uuid.UUID | None = None,
        include_archived: bool = False,
    ) -> Page[Project]:
        limit = max(1, min(limit, 200))
        async with self._db.tenant_session(org_id) as s:
            stmt = select(Project).where(Project.org_id == org_id, Project.deleted_at.is_(None))
            if not include_archived:
                stmt = stmt.where(Project.archived_at.is_(None))
            if cursor:
                stmt = stmt.where(Project.id < cursor)
            rows = list(
                (await s.execute(stmt.order_by(Project.id.desc()).limit(limit + 1))).scalars()
            )
        return Page(rows[:limit], rows[limit - 1].id if len(rows) > limit else None)

    async def _project(
        self, s: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID, *, lock: bool = False
    ) -> Project:
        stmt = select(Project).where(
            Project.id == project_id, Project.org_id == org_id, Project.deleted_at.is_(None)
        )
        project = (await s.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if project is None:
            raise _not_found("Project")
        return project

    async def get_project(self, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        async with self._db.tenant_session(org_id) as s:
            return await self._project(s, org_id, project_id)

    async def update_project(
        self,
        principal: Principal,
        project_id: uuid.UUID,
        *,
        name: str | None = None,
        description: Any = UNSET,
        settings: dict[str, Any] | None = None,
        archived: bool | None = None,
    ) -> Project:
        try:
            async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
                project = await self._project(s, principal.org_id, project_id, lock=True)
                changed: dict[str, Any] = {}
                if name is not None:
                    project.name = name.strip()
                    changed["name"] = True
                if description is not UNSET:
                    project.description = description
                    changed["description"] = True
                if settings is not None:
                    project.settings = settings
                    changed["settings"] = True
                if archived is not None:
                    project.archived_at = datetime.now(UTC) if archived else None
                    changed["archived"] = archived
                await s.flush()
                await self._record(
                    s,
                    principal,
                    "project.updated",
                    "project",
                    project_id,
                    event="project.updated",
                    diff=changed,
                )
                return project
        except IntegrityError as exc:
            raise self._project_conflict(exc) from None

    async def delete_project(self, principal: Principal, project_id: uuid.UUID) -> None:
        """Borrado lógico. Los assets del proyecto se conservan (sin carpeta de proyecto visible)."""
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            project = await self._project(s, principal.org_id, project_id, lock=True)
            project.deleted_at = datetime.now(UTC)
            await self._record(
                s, principal, "project.deleted", "project", project_id, event="project.deleted"
            )

    # ------------------------------------------------------------------------------ carpetas

    async def _folder(
        self, s: AsyncSession, org_id: uuid.UUID, folder_id: uuid.UUID, *, lock: bool = False
    ) -> Folder:
        stmt = select(Folder).where(Folder.id == folder_id, Folder.org_id == org_id)
        folder = (await s.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if folder is None:
            raise _not_found("Folder")
        return folder

    async def _depth(self, s: AsyncSession, org_id: uuid.UUID, folder_id: uuid.UUID | None) -> int:
        """Profundidad de la cadena de ancestros (0 si no hay). Detecta ciclos por tope."""
        depth, current = 0, folder_id
        while current is not None:
            depth += 1
            if depth > MAX_FOLDER_DEPTH + 1:
                break
            current = (
                await s.execute(
                    select(Folder.parent_id).where(Folder.id == current, Folder.org_id == org_id)
                )
            ).scalar_one_or_none()
        return depth

    async def create_folder(
        self,
        principal: Principal,
        *,
        name: str,
        project_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> Folder:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            if project_id is not None:
                await self._project(s, principal.org_id, project_id)
            if parent_id is not None:
                parent = await self._folder(s, principal.org_id, parent_id)
                if parent.project_id != project_id:
                    raise _invalid(
                        "folder_project_mismatch",
                        "La carpeta debe estar en el mismo proyecto que su carpeta padre.",
                    )
                if await self._depth(s, principal.org_id, parent_id) >= MAX_FOLDER_DEPTH:
                    raise _invalid(
                        "folder_too_deep", f"Máximo {MAX_FOLDER_DEPTH} niveles de carpetas."
                    )
            folder = Folder(
                id=new_id(),
                org_id=principal.org_id,
                project_id=project_id,
                parent_id=parent_id,
                name=name.strip(),
                created_by=principal.user_id,
            )
            try:
                s.add(folder)
                await s.flush()
            except IntegrityError as exc:
                raise self._folder_conflict(exc) from None
            await self._record(s, principal, "folder.created", "folder", folder.id)
            return folder

    @staticmethod
    def _folder_conflict(exc: IntegrityError) -> ApiError:
        if _constraint(exc) == "uq_folders__siblings":
            return _conflict("folder_name_taken", "Ya existe una carpeta con ese nombre aquí.")
        raise exc

    async def list_folders(
        self,
        org_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
        any_project: bool = False,
        any_parent: bool = False,
    ) -> list[Folder]:
        """Por defecto lista la raíz sin proyecto. ``any_*`` quitan el filtro de ese nivel."""
        async with self._db.tenant_session(org_id) as s:
            stmt = select(Folder).where(Folder.org_id == org_id)
            if not any_project:
                stmt = stmt.where(
                    Folder.project_id == project_id if project_id else Folder.project_id.is_(None)
                )
            if not any_parent:
                stmt = stmt.where(
                    Folder.parent_id == parent_id if parent_id else Folder.parent_id.is_(None)
                )
            return list((await s.execute(stmt.order_by(func.lower(Folder.name)))).scalars())

    async def update_folder(
        self,
        principal: Principal,
        folder_id: uuid.UUID,
        *,
        name: str | None = None,
        parent_id: Any = UNSET,
    ) -> Folder:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            folder = await self._folder(s, principal.org_id, folder_id, lock=True)
            if name is not None:
                folder.name = name.strip()
            if parent_id is not UNSET:
                if parent_id is not None:
                    parent = await self._folder(s, principal.org_id, parent_id)
                    if parent.project_id != folder.project_id:
                        raise _invalid(
                            "folder_project_mismatch",
                            "La carpeta debe estar en el mismo proyecto que su carpeta padre.",
                        )
                    if await self._is_descendant(s, principal.org_id, parent_id, folder_id):
                        raise _invalid(
                            "folder_cycle", "Una carpeta no puede moverse dentro de sí misma."
                        )
                    if await self._depth(s, principal.org_id, parent_id) >= MAX_FOLDER_DEPTH:
                        raise _invalid(
                            "folder_too_deep", f"Máximo {MAX_FOLDER_DEPTH} niveles de carpetas."
                        )
                folder.parent_id = parent_id
            try:
                await s.flush()
            except IntegrityError as exc:
                raise self._folder_conflict(exc) from None
            await self._record(s, principal, "folder.updated", "folder", folder_id)
            return folder

    async def _is_descendant(
        self, s: AsyncSession, org_id: uuid.UUID, node: uuid.UUID, ancestor: uuid.UUID
    ) -> bool:
        """¿``node`` es ``ancestor`` o cuelga de él?"""
        current: uuid.UUID | None = node
        for _ in range(MAX_FOLDER_DEPTH + 2):
            if current is None:
                return False
            if current == ancestor:
                return True
            current = (
                await s.execute(
                    select(Folder.parent_id).where(Folder.id == current, Folder.org_id == org_id)
                )
            ).scalar_one_or_none()
        return True  # cadena sospechosamente larga: se trata como ciclo

    async def delete_folder(self, principal: Principal, folder_id: uuid.UUID) -> None:
        """Borra la carpeta y sus subcarpetas; los assets quedan sin carpeta (FK ``SET NULL``)."""
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await self._folder(s, principal.org_id, folder_id)
            await s.execute(
                delete(Folder).where(Folder.id == folder_id, Folder.org_id == principal.org_id)
            )
            await self._record(s, principal, "folder.deleted", "folder", folder_id)

    # ------------------------------------------------------------------------------ etiquetas

    async def create_tag(self, principal: Principal, *, name: str) -> Tag:
        tag = Tag(id=new_id(), org_id=principal.org_id, name=name.strip())
        try:
            async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
                s.add(tag)
                await s.flush()
                await self._record(s, principal, "tag.created", "tag", tag.id)
        except IntegrityError as exc:
            if _constraint(exc) == "uq_tags__org_id_name":
                raise _conflict(
                    "tag_name_taken", "Ya existe una etiqueta con ese nombre."
                ) from None
            raise
        return tag

    async def list_tags(
        self, org_id: uuid.UUID, *, q: str | None = None, limit: int = 200
    ) -> list[Tag]:
        async with self._db.tenant_session(org_id) as s:
            stmt = select(Tag).where(Tag.org_id == org_id)
            if q:
                stmt = stmt.where(Tag.name.ilike(_like(q), escape="\\"))
            return list(
                (
                    await s.execute(
                        stmt.order_by(func.lower(Tag.name)).limit(max(1, min(limit, 500)))
                    )
                ).scalars()
            )

    async def delete_tag(self, principal: Principal, tag_id: uuid.UUID) -> None:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            found = (
                await s.execute(
                    select(Tag.id).where(Tag.id == tag_id, Tag.org_id == principal.org_id)
                )
            ).scalar_one_or_none()
            if found is None:
                raise _not_found("Tag")
            await s.execute(delete(Tag).where(Tag.id == tag_id, Tag.org_id == principal.org_id))
            await self._record(s, principal, "tag.deleted", "tag", tag_id)

    # ------------------------------------------------------------------------------- assets

    async def create_asset(
        self,
        principal: Principal,
        *,
        kind: str,
        title: str,
        storage_object_id: uuid.UUID,
        original_filename: str | None = None,
        project_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
        source_type: str = "upload",
    ) -> MediaAsset:
        """Registra un asset ``uploading`` apuntando a un objeto reservado (lo usa la subida)."""
        if kind not in ASSET_KINDS:
            raise _invalid("invalid_asset_kind", f"Tipo no permitido: {kind}.")
        if source_type not in SOURCE_TYPES:
            raise _invalid("invalid_source_type", f"Origen no permitido: {source_type}.")
        await self._storage.get(principal.org_id, storage_object_id)  # 404 si no es de la org
        asset = MediaAsset(
            id=new_id(),
            org_id=principal.org_id,
            project_id=project_id,
            folder_id=folder_id,
            kind=kind,
            source_type=source_type,
            title=title.strip(),
            original_filename=original_filename,
            status="uploading",
            storage_object_id=storage_object_id,
            created_by=principal.user_id,
        )
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await self._validate_placement(s, principal.org_id, project_id, folder_id)
            s.add(asset)
            await s.flush()
            await self._record(
                s,
                principal,
                "asset.created",
                "asset",
                asset.id,
                event="asset.created",
                diff={"kind": kind, "status": asset.status},
            )
        return asset

    async def _validate_placement(
        self,
        s: AsyncSession,
        org_id: uuid.UUID,
        project_id: uuid.UUID | None,
        folder_id: uuid.UUID | None,
    ) -> None:
        if project_id is not None:
            await self._project(s, org_id, project_id)
        if folder_id is not None:
            folder = await self._folder(s, org_id, folder_id)
            if folder.project_id != project_id:
                raise _invalid(
                    "folder_project_mismatch",
                    "La carpeta pertenece a otro proyecto: asigna primero el proyecto correcto.",
                )

    async def _asset(
        self,
        s: AsyncSession,
        org_id: uuid.UUID,
        asset_id: uuid.UUID,
        *,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> MediaAsset:
        stmt = select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.org_id == org_id)
        if not include_deleted:
            stmt = stmt.where(MediaAsset.deleted_at.is_(None))
        asset = (await s.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if asset is None:
            raise _not_found("Asset")
        return asset

    async def _tags_of(
        self, s: AsyncSession, org_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[Tag]]:
        if not asset_ids:
            return {}
        rows = (
            await s.execute(
                select(AssetTag.asset_id, Tag)
                .join(Tag, and_(Tag.id == AssetTag.tag_id, Tag.org_id == AssetTag.org_id))
                .where(AssetTag.org_id == org_id, AssetTag.asset_id.in_(asset_ids))
                .order_by(func.lower(Tag.name))
            )
        ).all()
        out: dict[uuid.UUID, list[Tag]] = {}
        for asset_id, tag in rows:
            out.setdefault(asset_id, []).append(tag)
        return out

    async def get_asset(
        self, org_id: uuid.UUID, asset_id: uuid.UUID, *, include_deleted: bool = False
    ) -> AssetView:
        async with self._db.tenant_session(org_id) as s:
            asset = await self._asset(s, org_id, asset_id, include_deleted=include_deleted)
            tags = (await self._tags_of(s, org_id, [asset_id])).get(asset_id, [])
            return AssetView(asset, tags)

    async def list_assets(
        self,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        cursor: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
        kind: str | None = None,
        status: str | None = None,
        q: str | None = None,
        tag_ids: list[uuid.UUID] | None = None,
    ) -> Page[AssetView]:
        limit = max(1, min(limit, 200))
        async with self._db.tenant_session(org_id) as s:
            stmt = select(MediaAsset).where(
                MediaAsset.org_id == org_id, MediaAsset.deleted_at.is_(None)
            )
            if project_id:
                stmt = stmt.where(MediaAsset.project_id == project_id)
            if folder_id:
                stmt = stmt.where(MediaAsset.folder_id == folder_id)
            if kind:
                stmt = stmt.where(MediaAsset.kind == kind)
            if status:
                stmt = stmt.where(MediaAsset.status == status)
            if q and q.strip():
                term = q.strip()
                stmt = stmt.where(
                    or_(
                        MediaAsset.tsv.op("@@")(func.websearch_to_tsquery("spanish", term)),
                        MediaAsset.title.ilike(_like(term), escape="\\"),
                    )
                )
            for tag_id in dict.fromkeys(tag_ids or []):  # todas las etiquetas indicadas
                stmt = stmt.where(
                    select(AssetTag.asset_id)
                    .where(
                        AssetTag.asset_id == MediaAsset.id,
                        AssetTag.tag_id == tag_id,
                        AssetTag.org_id == org_id,
                    )
                    .exists()
                )
            if cursor:
                stmt = stmt.where(MediaAsset.id < cursor)
            rows = list(
                (await s.execute(stmt.order_by(MediaAsset.id.desc()).limit(limit + 1))).scalars()
            )
            page = rows[:limit]
            tags = await self._tags_of(s, org_id, [a.id for a in page])
        return Page(
            [AssetView(a, tags.get(a.id, [])) for a in page],
            rows[limit - 1].id if len(rows) > limit else None,
        )

    async def update_asset(
        self,
        principal: Principal,
        asset_id: uuid.UUID,
        *,
        title: str | None = None,
        project_id: Any = UNSET,
        folder_id: Any = UNSET,
        tag_ids: list[uuid.UUID] | None = None,
    ) -> AssetView:
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            asset = await self._asset(s, principal.org_id, asset_id, lock=True)
            new_project = asset.project_id if project_id is UNSET else project_id
            new_folder = asset.folder_id if folder_id is UNSET else folder_id
            if project_id is not UNSET and folder_id is UNSET and asset.folder_id is not None:
                new_folder = None  # cambiar de proyecto saca al asset de su carpeta anterior
            if project_id is not UNSET or folder_id is not UNSET:
                await self._validate_placement(s, principal.org_id, new_project, new_folder)
            changed: dict[str, Any] = {}
            if title is not None:
                asset.title = title.strip()
                changed["title"] = True
            if project_id is not UNSET or folder_id is not UNSET:
                asset.project_id, asset.folder_id = new_project, new_folder
                changed["placement"] = True
            if tag_ids is not None:
                await self._replace_tags(s, principal.org_id, asset_id, tag_ids)
                changed["tags"] = len(set(tag_ids))
            await s.flush()
            await self._record(
                s,
                principal,
                "asset.updated",
                "asset",
                asset_id,
                event="asset.updated",
                diff=changed,
            )
            tags = (await self._tags_of(s, principal.org_id, [asset_id])).get(asset_id, [])
            return AssetView(asset, tags)

    async def _replace_tags(
        self, s: AsyncSession, org_id: uuid.UUID, asset_id: uuid.UUID, tag_ids: list[uuid.UUID]
    ) -> None:
        wanted = set(tag_ids)
        if wanted:
            found = set(
                (
                    await s.execute(select(Tag.id).where(Tag.org_id == org_id, Tag.id.in_(wanted)))
                ).scalars()
            )
            if found != wanted:
                raise _invalid("unknown_tag", "Alguna etiqueta no existe en esta organización.")
        await s.execute(
            delete(AssetTag).where(AssetTag.asset_id == asset_id, AssetTag.org_id == org_id)
        )
        for tag_id in wanted:
            s.add(AssetTag(org_id=org_id, asset_id=asset_id, tag_id=tag_id))
        await s.flush()

    async def delete_asset(self, principal: Principal, asset_id: uuid.UUID) -> None:
        """Borrado lógico: desaparece de la biblioteca; la retención lo elimina después."""
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            asset = await self._asset(s, principal.org_id, asset_id, lock=True)
            asset.deleted_at = datetime.now(UTC)
            await self._record(
                s, principal, "asset.deleted", "asset", asset_id, event="asset.deleted"
            )

    async def download_url(
        self, org_id: uuid.UUID, asset_id: uuid.UUID, *, filename: str | None = None
    ) -> str:
        view = await self.get_asset(org_id, asset_id)
        if view.asset.status == "uploading":
            raise _conflict("asset_not_ready", "El archivo todavía se está subiendo.")
        return await self._storage.presign_download(
            org_id,
            view.asset.storage_object_id,
            filename=filename or view.asset.original_filename or view.asset.title,
        )

    # -------------------------------------------------- estado de procesamiento (job media.probe)

    async def transition_asset(
        self,
        org_id: uuid.UUID,
        asset_id: uuid.UUID,
        to: Literal["processing", "ready", "failed"],
        *,
        probe: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        **metadata: Any,
    ) -> MediaAsset:
        """Cambia el estado respetando ``ASSET_TRANSITIONS``. ``metadata`` admite los campos
        técnicos (duration_ms, width, height, fps, codecs, container, bitrate_kbps, has_audio)."""
        allowed_fields = {
            "duration_ms",
            "width",
            "height",
            "fps",
            "video_codec",
            "audio_codec",
            "container",
            "bitrate_kbps",
            "has_audio",
        }
        unknown = set(metadata) - allowed_fields
        if unknown:
            raise _invalid("invalid_asset_metadata", f"Campos no permitidos: {sorted(unknown)}.")
        async with self._db.tenant_session(org_id) as s:
            asset = await self._asset(s, org_id, asset_id, lock=True, include_deleted=True)
            if to not in ASSET_TRANSITIONS[asset.status]:
                raise _conflict(
                    "invalid_asset_transition", f"No se puede pasar de {asset.status} a {to}."
                )
            if to == "ready":
                if asset.kind != "image" and metadata.get("duration_ms") is None:
                    raise _invalid("missing_duration", "Un asset listo necesita su duración.")
                for key, value in metadata.items():
                    setattr(asset, key, value)
                asset.probe = probe
                asset.error = None
            elif to == "failed":
                asset.error = error or {"code": "unknown"}
            else:
                asset.error = None
            asset.status = to
            try:
                await s.flush()
            except IntegrityError as exc:
                if _constraint(exc) in (
                    "ck_media_assets__ready_object",
                    "ck_media_assets__ready_has_duration",
                ):
                    raise _conflict(
                        "asset_object_not_ready", "El archivo del asset aún no está confirmado."
                    ) from None
                raise
            await emit(
                s,
                type="asset.status_changed",
                org_id=org_id,
                aggregate_type="asset",
                aggregate_id=asset_id,
                payload={"id": str(asset_id), "status": to},
            )
            return asset

    # ------------------------------------------------------------------------------ derivados

    async def upsert_derivative(
        self,
        org_id: uuid.UUID,
        asset_id: uuid.UUID,
        kind: str,
        storage_object_id: uuid.UUID,
        meta: dict[str, Any] | None = None,
    ) -> MediaDerivative:
        """Crea o reemplaza el derivado. El conteo de referencias lo mantiene el trigger."""
        if kind not in DERIVATIVE_KINDS:
            raise _invalid("invalid_derivative_kind", f"Derivado desconocido: {kind}.")
        await self._storage.get(org_id, storage_object_id)
        async with self._db.tenant_session(org_id) as s:
            await self._asset(s, org_id, asset_id, include_deleted=True)
            row = (
                await s.execute(
                    select(MediaDerivative)
                    .where(MediaDerivative.asset_id == asset_id, MediaDerivative.kind == kind)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                row = MediaDerivative(
                    id=new_id(),
                    org_id=org_id,
                    asset_id=asset_id,
                    kind=kind,
                    storage_object_id=storage_object_id,
                    meta=meta or {},
                )
                s.add(row)
            else:
                row.storage_object_id = storage_object_id
                row.meta = meta or {}
            await s.flush()
            return row

    async def list_derivatives(
        self, org_id: uuid.UUID, asset_id: uuid.UUID
    ) -> list[MediaDerivative]:
        async with self._db.tenant_session(org_id) as s:
            await self._asset(s, org_id, asset_id)
            return list(
                (
                    await s.execute(
                        select(MediaDerivative)
                        .where(
                            MediaDerivative.org_id == org_id, MediaDerivative.asset_id == asset_id
                        )
                        .order_by(MediaDerivative.kind)
                    )
                ).scalars()
            )

    # ------------------------------------------------------------------------------- subidas

    async def create_upload(
        self,
        principal: Principal,
        *,
        asset_id: uuid.UUID,
        storage_object_id: uuid.UUID,
        s3_upload_id: str | None,
        part_size: int | None,
        size_bytes: int | None,
        expires_at: datetime,
    ) -> Upload:
        upload = Upload(
            id=new_id(),
            org_id=principal.org_id,
            asset_id=asset_id,
            storage_object_id=storage_object_id,
            s3_upload_id=s3_upload_id,
            part_size=part_size,
            size_bytes=size_bytes,
            parts=[],
            status="open",
            expires_at=expires_at,
            created_by=principal.user_id,
        )
        async with self._db.tenant_session(principal.org_id, principal.user_id) as s:
            await self._asset(s, principal.org_id, asset_id)
            s.add(upload)
            await s.flush()
        return upload

    async def get_upload(self, org_id: uuid.UUID, upload_id: uuid.UUID) -> Upload:
        async with self._db.tenant_session(org_id) as s:
            upload = (
                await s.execute(
                    select(Upload).where(Upload.id == upload_id, Upload.org_id == org_id)
                )
            ).scalar_one_or_none()
            if upload is None:
                raise _not_found("Upload")
            return upload

    async def transition_upload(
        self,
        org_id: uuid.UUID,
        upload_id: uuid.UUID,
        to: str,
        *,
        parts: list[dict[str, Any]] | None = None,
    ) -> Upload:
        async with self._db.tenant_session(org_id) as s:
            upload = (
                await s.execute(
                    select(Upload)
                    .where(Upload.id == upload_id, Upload.org_id == org_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if upload is None:
                raise _not_found("Upload")
            if to not in UPLOAD_TRANSITIONS[upload.status]:
                raise _conflict(
                    "invalid_upload_transition", f"No se puede pasar de {upload.status} a {to}."
                )
            upload.status = to
            if parts is not None:
                upload.parts = parts
            await s.flush()
            return upload

    async def expire_stale_uploads(self, org_id: uuid.UUID, now: datetime | None = None) -> int:
        """Marca como ``expired`` las subidas abiertas vencidas. Devuelve cuántas."""
        async with self._db.tenant_session(org_id) as s:
            result = await s.execute(
                update(Upload)
                .where(
                    Upload.org_id == org_id,
                    Upload.status == "open",
                    Upload.expires_at < (now or datetime.now(UTC)),
                )
                .values(status="expired")
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]
