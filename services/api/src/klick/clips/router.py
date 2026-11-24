"""API de clips (docs/05 §2: "Clips y render"). Permisos `clips:read|write`.

El documento de edición se guarda con `PATCH /clips/{id}/edit-project` y la cabecera
`If-Match: "<revision>"`: si la revisión ya no es la vigente responde 412 (nadie pisa a nadie).
El render y las plantillas llegan con F1-08 en adelante.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from klick.clips.models import Clip, ClipVersion
from klick.clips.service import ClipService, ClipView
from klick.media.service import UNSET
from klick.orgs.deps import CurrentPrincipal

clips_router = APIRouter(prefix="/clips", tags=["clips"])
READ, WRITE = "clips:read", "clips:write"


def get_clip_service(request: Request) -> ClipService:
    service: ClipService = request.app.state.clip_service
    return service


Clips = Annotated[ClipService, Depends(get_clip_service)]
Limit = Annotated[int, Query(ge=1, le=200)]


def _etag(revision: int) -> str:
    return f'"{revision}"'


# ------------------------------------------------------------------------------- esquemas


class ClipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_asset_id: uuid.UUID
    start_ms: int = Field(ge=0, le=86_400_000)
    end_ms: int = Field(ge=1, le=86_400_000)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    project_id: uuid.UUID | None = None
    aspect: Literal["9x16", "16x9", "1x1", "4x5"] = "9x16"

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("El título no puede estar vacío.")
        return value


class ClipUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: Literal["draft", "archived"] | None = None
    project_id: uuid.UUID | None = None  # null explícito = sacar del proyecto

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("El título no puede estar vacío.")
        return value


class ClipOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID | None
    source_asset_id: uuid.UUID
    title: str
    status: str
    current_version_id: uuid.UUID | None
    revision: int | None
    created_at: datetime
    updated_at: datetime | None

    @classmethod
    def of(cls, clip: Clip, revision: int | None = None) -> "ClipOut":
        return cls(
            id=clip.id,
            project_id=clip.project_id,
            source_asset_id=clip.source_asset_id,
            title=clip.title,
            status=clip.status,
            current_version_id=clip.current_version_id,
            revision=revision,
            created_at=clip.created_at,
            updated_at=clip.updated_at,
        )

    @classmethod
    def of_view(cls, view: ClipView) -> "ClipOut":
        return cls.of(view.clip, view.revision)


class ClipPage(BaseModel):
    items: list[ClipOut]
    next_cursor: uuid.UUID | None


class EditProjectOut(BaseModel):
    clip_id: uuid.UUID
    schema_version: str
    revision: int
    document: dict[str, Any]
    updated_at: datetime | None


class EditProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any]


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=200)


class VersionOut(BaseModel):
    id: uuid.UUID
    version: int
    schema_version: str
    label: str | None
    created_at: datetime

    @classmethod
    def of(cls, v: ClipVersion) -> "VersionOut":
        return cls(
            id=v.id,
            version=v.version,
            schema_version=v.schema_version,
            label=v.label,
            created_at=v.created_at,
        )


class VersionDetail(VersionOut):
    document: dict[str, Any]


class VersionPage(BaseModel):
    items: list[VersionOut]
    next_before: int | None


# --------------------------------------------------------------------------------- clips


@clips_router.get("", response_model=ClipPage)
async def list_clips(
    principal: CurrentPrincipal,
    clips: Clips,
    limit: Limit = 50,
    cursor: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    source_asset_id: uuid.UUID | None = None,
    clip_status: Annotated[
        Literal["draft", "ready", "archived"] | None, Query(alias="status")
    ] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> ClipPage:
    principal.require(READ)
    page = await clips.list_clips(
        principal.org_id,
        limit=limit,
        cursor=cursor,
        project_id=project_id,
        source_asset_id=source_asset_id,
        status=clip_status,
        q=q,
    )
    return ClipPage(items=[ClipOut.of(c) for c in page.items], next_cursor=page.next_cursor)


@clips_router.post("", status_code=status.HTTP_201_CREATED, response_model=ClipOut)
async def create_clip(
    body: ClipCreate, response: Response, principal: CurrentPrincipal, clips: Clips
) -> ClipOut:
    principal.require(WRITE)
    view = await clips.create_clip(
        principal,
        source_asset_id=body.source_asset_id,
        start_ms=body.start_ms,
        end_ms=body.end_ms,
        title=body.title,
        project_id=body.project_id,
        aspect=body.aspect,
    )
    response.headers["ETag"] = _etag(view.revision)
    return ClipOut.of_view(view)


@clips_router.get("/{clip_id}", response_model=ClipOut)
async def get_clip(
    clip_id: uuid.UUID, response: Response, principal: CurrentPrincipal, clips: Clips
) -> ClipOut:
    principal.require(READ)
    view = await clips.get_clip(principal.org_id, clip_id)
    response.headers["ETag"] = _etag(view.revision)
    return ClipOut.of_view(view)


@clips_router.patch("/{clip_id}", response_model=ClipOut)
async def update_clip(
    clip_id: uuid.UUID, body: ClipUpdate, principal: CurrentPrincipal, clips: Clips
) -> ClipOut:
    principal.require(WRITE)
    sent = body.model_fields_set
    view = await clips.update_clip(
        principal,
        clip_id,
        title=body.title,
        status=body.status,
        project_id=body.project_id if "project_id" in sent else UNSET,
    )
    return ClipOut.of_view(view)


@clips_router.delete("/{clip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clip(clip_id: uuid.UUID, principal: CurrentPrincipal, clips: Clips) -> Response:
    principal.require(WRITE)
    await clips.delete_clip(principal, clip_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------- documento de edición


@clips_router.get("/{clip_id}/edit-project", response_model=EditProjectOut)
async def get_edit_project(
    clip_id: uuid.UUID, response: Response, principal: CurrentPrincipal, clips: Clips
) -> EditProjectOut:
    principal.require(READ)
    row = await clips.get_edit_project(principal.org_id, clip_id)
    response.headers["ETag"] = _etag(row.revision)
    return EditProjectOut(
        clip_id=row.clip_id,
        schema_version=row.schema_version,
        revision=row.revision,
        document=row.document,
        updated_at=row.updated_at,
    )


@clips_router.patch("/{clip_id}/edit-project", response_model=EditProjectOut)
async def update_edit_project(
    clip_id: uuid.UUID,
    body: EditProjectUpdate,
    response: Response,
    principal: CurrentPrincipal,
    clips: Clips,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> EditProjectOut:
    principal.require(WRITE)
    row = await clips.update_edit_project(principal, clip_id, body.document, if_match)
    response.headers["ETag"] = _etag(row.revision)
    return EditProjectOut(
        clip_id=row.clip_id,
        schema_version=row.schema_version,
        revision=row.revision,
        document=row.document,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------- versiones


@clips_router.get("/{clip_id}/versions", response_model=VersionPage)
async def list_versions(
    clip_id: uuid.UUID,
    principal: CurrentPrincipal,
    clips: Clips,
    limit: Limit = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> VersionPage:
    principal.require(READ)
    rows, next_before = await clips.list_versions(
        principal.org_id, clip_id, limit=limit, before=before
    )
    return VersionPage(items=[VersionOut.of(v) for v in rows], next_before=next_before)


@clips_router.post(
    "/{clip_id}/versions", status_code=status.HTTP_201_CREATED, response_model=VersionOut
)
async def create_version(
    clip_id: uuid.UUID, body: VersionCreate, principal: CurrentPrincipal, clips: Clips
) -> VersionOut:
    principal.require(WRITE)
    return VersionOut.of(await clips.create_version(principal, clip_id, label=body.label))


@clips_router.get("/{clip_id}/versions/{version}", response_model=VersionDetail)
async def get_version(
    clip_id: uuid.UUID,
    version: Annotated[int, Path(ge=1)],
    principal: CurrentPrincipal,
    clips: Clips,
) -> VersionDetail:
    principal.require(READ)
    v = await clips.get_version(principal.org_id, clip_id, version)
    return VersionDetail(**VersionOut.of(v).model_dump(), document=v.document)
