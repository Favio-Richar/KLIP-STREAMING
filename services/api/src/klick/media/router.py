"""API de la biblioteca de medios (docs/05 §2: "Proyectos y biblioteca"). Permisos `media:*`.

La subida multipart (`POST /assets/uploads`...) llega con F1-02.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from klick.core.idempotency import Idem, validate_key
from klick.media.service import UNSET, AssetView, MediaService
from klick.media.uploads import UploadService, UploadState
from klick.orgs.deps import CurrentPrincipal

projects_router = APIRouter(prefix="/projects", tags=["media"])
folders_router = APIRouter(prefix="/folders", tags=["media"])
tags_router = APIRouter(prefix="/tags", tags=["media"])
assets_router = APIRouter(prefix="/assets", tags=["media"])
# Se incluye ANTES que assets_router: `/assets/uploads/...` no debe tomarse como `/assets/{asset_id}`.
uploads_router = APIRouter(prefix="/assets/uploads", tags=["media"])


def get_media_service(request: Request) -> MediaService:
    service: MediaService = request.app.state.media_service
    return service


def get_upload_service(request: Request) -> UploadService:
    service: UploadService = request.app.state.upload_service
    return service


Media = Annotated[MediaService, Depends(get_media_service)]
Uploads = Annotated[UploadService, Depends(get_upload_service)]
Limit = Annotated[int, Query(ge=1, le=200)]

READ, WRITE = "media:read", "media:write"


def _name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("El nombre no puede estar vacío.")
    return value


# ------------------------------------------------------------------------------- esquemas


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    settings: dict[str, Any] = Field(default_factory=dict)

    _strip = field_validator("name")(_name)


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    settings: dict[str, Any] | None = None
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return None if value is None else _name(value)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    settings: dict[str, Any]
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime | None


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: uuid.UUID | None


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    project_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None

    _strip = field_validator("name")(_name)


class FolderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None  # null explícito = mover a la raíz

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return None if value is None else _name(value)


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    name: str
    created_at: datetime


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)

    _strip = field_validator("name")(_name)


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class AssetOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    folder_id: uuid.UUID | None
    kind: str
    source_type: str
    title: str
    original_filename: str | None
    status: str
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    container: str | None
    bitrate_kbps: int | None
    has_audio: bool | None
    error: dict[str, Any] | None
    tags: list[TagOut]
    thumbnail_url: str | None = None
    created_at: datetime
    updated_at: datetime | None

    @classmethod
    def of(cls, view: AssetView, thumbnail_url: str | None = None) -> "AssetOut":
        a = view.asset
        return cls(
            id=a.id,
            project_id=a.project_id,
            folder_id=a.folder_id,
            kind=a.kind,
            source_type=a.source_type,
            title=a.title,
            original_filename=a.original_filename,
            status=a.status,
            duration_ms=a.duration_ms,
            width=a.width,
            height=a.height,
            fps=float(a.fps) if a.fps is not None else None,
            video_codec=a.video_codec,
            audio_codec=a.audio_codec,
            container=a.container,
            bitrate_kbps=a.bitrate_kbps,
            has_audio=a.has_audio,
            error=a.error,
            tags=[TagOut.model_validate(t) for t in view.tags],
            thumbnail_url=thumbnail_url,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )


class AssetPage(BaseModel):
    items: list[AssetOut]
    next_cursor: uuid.UUID | None


class AssetUpdate(BaseModel):
    """Solo cambia lo enviado: `project_id: null` saca al asset del proyecto."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    project_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    tag_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return None if value is None else _name(value)


class DerivativeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    meta: dict[str, Any]
    created_at: datetime


class DownloadUrl(BaseModel):
    url: str
    expires_in: int


# ---------------------------------------------------------------------------------- proyectos


@projects_router.get("", response_model=ProjectPage)
async def list_projects(
    principal: CurrentPrincipal,
    media: Media,
    limit: Limit = 50,
    cursor: uuid.UUID | None = None,
    include_archived: bool = False,
) -> ProjectPage:
    principal.require(READ)
    page = await media.list_projects(
        principal.org_id, limit=limit, cursor=cursor, include_archived=include_archived
    )
    return ProjectPage(
        items=[ProjectOut.model_validate(p) for p in page.items], next_cursor=page.next_cursor
    )


@projects_router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectOut)
async def create_project(body: ProjectCreate, principal: CurrentPrincipal, media: Media) -> Any:
    principal.require(WRITE)
    return await media.create_project(
        principal, name=body.name, description=body.description, settings=body.settings
    )


@projects_router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, principal: CurrentPrincipal, media: Media) -> Any:
    principal.require(READ)
    return await media.get_project(principal.org_id, project_id)


@projects_router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, principal: CurrentPrincipal, media: Media
) -> Any:
    principal.require(WRITE)
    sent = body.model_fields_set
    return await media.update_project(
        principal,
        project_id,
        name=body.name,
        description=body.description if "description" in sent else UNSET,
        settings=body.settings,
        archived=body.archived,
    )


@projects_router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, principal: CurrentPrincipal, media: Media
) -> Response:
    principal.require(WRITE)
    await media.delete_project(principal, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------------- carpetas


@folders_router.get("", response_model=list[FolderOut])
async def list_folders(
    principal: CurrentPrincipal,
    media: Media,
    project_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    recursive: Annotated[
        bool, Query(description="Todas las carpetas del proyecto, sin importar el nivel")
    ] = False,
) -> Any:
    principal.require(READ)
    return await media.list_folders(
        principal.org_id, project_id=project_id, parent_id=parent_id, any_parent=recursive
    )


@folders_router.post("", status_code=status.HTTP_201_CREATED, response_model=FolderOut)
async def create_folder(body: FolderCreate, principal: CurrentPrincipal, media: Media) -> Any:
    principal.require(WRITE)
    return await media.create_folder(
        principal, name=body.name, project_id=body.project_id, parent_id=body.parent_id
    )


@folders_router.patch("/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: uuid.UUID, body: FolderUpdate, principal: CurrentPrincipal, media: Media
) -> Any:
    principal.require(WRITE)
    return await media.update_folder(
        principal,
        folder_id,
        name=body.name,
        parent_id=body.parent_id if "parent_id" in body.model_fields_set else UNSET,
    )


@folders_router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: uuid.UUID, principal: CurrentPrincipal, media: Media
) -> Response:
    principal.require(WRITE)
    await media.delete_folder(principal, folder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------------- etiquetas


@tags_router.get("", response_model=list[TagOut])
async def list_tags(
    principal: CurrentPrincipal, media: Media, q: Annotated[str | None, Query(max_length=60)] = None
) -> Any:
    principal.require(READ)
    return await media.list_tags(principal.org_id, q=q)


@tags_router.post("", status_code=status.HTTP_201_CREATED, response_model=TagOut)
async def create_tag(body: TagCreate, principal: CurrentPrincipal, media: Media) -> Any:
    principal.require(WRITE)
    return await media.create_tag(principal, name=body.name)


@tags_router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: uuid.UUID, principal: CurrentPrincipal, media: Media) -> Response:
    principal.require(WRITE)
    await media.delete_tag(principal, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -------------------------------------------------------------------------------------- assets


@assets_router.get("", response_model=AssetPage)
async def list_assets(
    principal: CurrentPrincipal,
    media: Media,
    limit: Limit = 50,
    cursor: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    folder_id: uuid.UUID | None = None,
    kind: Literal["video", "audio", "image"] | None = None,
    asset_status: Annotated[
        Literal["uploading", "processing", "ready", "failed"] | None, Query(alias="status")
    ] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag_id: Annotated[list[uuid.UUID] | None, Query(max_length=20)] = None,
) -> AssetPage:
    principal.require(READ)
    page = await media.list_assets(
        principal.org_id,
        limit=limit,
        cursor=cursor,
        project_id=project_id,
        folder_id=folder_id,
        kind=kind,
        status=asset_status,
        q=q,
        tag_ids=tag_id,
    )
    thumbs = await media.thumbnail_urls(principal.org_id, [v.asset.id for v in page.items])
    return AssetPage(
        items=[AssetOut.of(v, thumbs.get(v.asset.id)) for v in page.items],
        next_cursor=page.next_cursor,
    )


@assets_router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, principal: CurrentPrincipal, media: Media) -> AssetOut:
    principal.require(READ)
    thumbs = await media.thumbnail_urls(principal.org_id, [asset_id])
    return AssetOut.of(await media.get_asset(principal.org_id, asset_id), thumbs.get(asset_id))


@assets_router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: uuid.UUID, body: AssetUpdate, principal: CurrentPrincipal, media: Media
) -> AssetOut:
    principal.require(WRITE)
    sent = body.model_fields_set
    view = await media.update_asset(
        principal,
        asset_id,
        title=body.title,
        project_id=body.project_id if "project_id" in sent else UNSET,
        folder_id=body.folder_id if "folder_id" in sent else UNSET,
        tag_ids=body.tag_ids,
    )
    return AssetOut.of(view)


@assets_router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: uuid.UUID, principal: CurrentPrincipal, media: Media) -> Response:
    principal.require(WRITE)
    await media.delete_asset(principal, asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@assets_router.post(
    "/{asset_id}/reprocess", status_code=status.HTTP_202_ACCEPTED, response_model=AssetOut
)
async def reprocess_asset(
    asset_id: uuid.UUID, principal: CurrentPrincipal, media: Media
) -> AssetOut:
    """Vuelve a analizar el archivo y regenerar sus derivados (para un asset fallido o listo)."""
    principal.require(WRITE)
    return AssetOut.of(await media.reprocess_asset(principal, asset_id))


@assets_router.get("/{asset_id}/derivatives", response_model=list[DerivativeOut])
async def list_derivatives(asset_id: uuid.UUID, principal: CurrentPrincipal, media: Media) -> Any:
    principal.require(READ)
    return await media.list_derivatives(principal.org_id, asset_id)


@assets_router.get("/{asset_id}/derivatives/{kind}/download-url", response_model=DownloadUrl)
async def derivative_download_url(
    asset_id: uuid.UUID,
    kind: Literal["proxy_720p", "thumbnail", "sprite", "waveform", "audio_16k"],
    request: Request,
    principal: CurrentPrincipal,
    media: Media,
) -> DownloadUrl:
    principal.require(READ)
    url = await media.derivative_url(principal.org_id, asset_id, kind)
    return DownloadUrl(url=url, expires_in=request.app.state.settings.storage_url_ttl_s)


@assets_router.get("/{asset_id}/download-url", response_model=DownloadUrl)
async def download_url(
    asset_id: uuid.UUID, request: Request, principal: CurrentPrincipal, media: Media
) -> DownloadUrl:
    principal.require(READ)
    url = await media.download_url(principal.org_id, asset_id)
    return DownloadUrl(url=url, expires_in=request.app.state.settings.storage_url_ttl_s)


# ------------------------------------------------------------------------------------ subidas


class UploadStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=300)
    size_bytes: int = Field(ge=1)
    content_type: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=300)
    project_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    sha256: str | None = Field(default=None, max_length=64)
    rights_confirmed: bool = Field(
        description="Confirmación explícita: 'tengo derecho a usar este contenido'."
    )


class UploadInfo(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    status: str
    part_size: int
    total_parts: int
    size_bytes: int
    uploaded_parts: list[int]
    expires_at: datetime

    @classmethod
    def of(cls, st: UploadState) -> "UploadInfo":
        u = st.upload
        return cls(
            id=u.id,
            asset_id=u.asset_id,
            status=u.status,
            part_size=u.part_size or 0,
            total_parts=st.total_parts,
            size_bytes=u.size_bytes or 0,
            uploaded_parts=st.uploaded_parts,
            expires_at=u.expires_at,
        )


class UploadStarted(BaseModel):
    deduplicated: bool
    upload: UploadInfo | None
    asset: AssetOut


class PartsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_numbers: list[int] = Field(min_length=1, max_length=200)


class PartUrls(BaseModel):
    urls: dict[int, str]
    expires_in: int


@uploads_router.post("", status_code=status.HTTP_201_CREATED, response_model=UploadStarted)
async def start_upload(
    body: UploadStart,
    request: Request,
    principal: CurrentPrincipal,
    uploads: Uploads,
    media: Media,
    idem: Idem,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    principal.require(WRITE)
    if idempotency_key:
        validate_key(idempotency_key)

    async def produce() -> tuple[int, Any]:
        result = await uploads.start(
            principal,
            filename=body.filename,
            size_bytes=body.size_bytes,
            content_type=body.content_type,
            rights_confirmed=body.rights_confirmed,
            title=body.title,
            project_id=body.project_id,
            folder_id=body.folder_id,
            sha256=body.sha256,
        )
        view = await media.get_asset(principal.org_id, result.asset.id)
        info = None
        if result.upload is not None:
            info = UploadInfo.of(await uploads.state(principal.org_id, result.upload.id))
        out = UploadStarted(deduplicated=result.deduplicated, upload=info, asset=AssetOut.of(view))
        return 201, out.model_dump(mode="json")

    return await idem.run(
        request, principal.org_id, "uploads.start", body.model_dump(mode="json"), produce
    )


@uploads_router.get("/{upload_id}", response_model=UploadInfo)
async def get_upload(upload_id: uuid.UUID, principal: CurrentPrincipal, uploads: Uploads) -> Any:
    principal.require(WRITE)
    return UploadInfo.of(await uploads.state(principal.org_id, upload_id))


@uploads_router.post("/{upload_id}/parts", response_model=PartUrls)
async def upload_part_urls(
    upload_id: uuid.UUID, body: PartsRequest, principal: CurrentPrincipal, uploads: Uploads
) -> PartUrls:
    principal.require(WRITE)
    urls, ttl = await uploads.part_urls(principal.org_id, upload_id, body.part_numbers)
    return PartUrls(urls=urls, expires_in=ttl)


@uploads_router.post("/{upload_id}/complete", response_model=AssetOut)
async def complete_upload(
    upload_id: uuid.UUID, principal: CurrentPrincipal, uploads: Uploads
) -> AssetOut:
    principal.require(WRITE)
    return AssetOut.of(await uploads.complete(principal, upload_id))


@uploads_router.post("/{upload_id}/abort", status_code=status.HTTP_204_NO_CONTENT)
async def abort_upload(
    upload_id: uuid.UUID, principal: CurrentPrincipal, uploads: Uploads
) -> Response:
    principal.require(WRITE)
    await uploads.abort(principal, upload_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
