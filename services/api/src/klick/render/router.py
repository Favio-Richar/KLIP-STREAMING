"""API de exportación (docs/05 §2: "Clips y render"). Permisos `clips:read|write`.

`POST /clips/{id}/render` es idempotente por (versión del clip, preset): pedirlo dos veces devuelve
el mismo trabajo. El progreso llega por SSE (`render.progress`) y la descarga se hace con
`GET /assets/{output_asset_id}/download-url` cuando el trabajo termina.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from klick.orgs.deps import CurrentPrincipal
from klick.render.models import RenderPresetRow
from klick.render.service import DEFAULT_PRESET, RenderService, RenderView

render_router = APIRouter(tags=["render"])
READ, WRITE = "clips:read", "clips:write"


def get_render_service(request: Request) -> RenderService:
    service: RenderService = request.app.state.render_service
    return service


Renders = Annotated[RenderService, Depends(get_render_service)]


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_key: str = Field(default=DEFAULT_PRESET, min_length=1, max_length=60)


class PresetOut(BaseModel):
    key: str
    width: int
    height: int
    fps: int
    container: str
    video_codec: str
    audio_codec: str
    crf: int
    audio_bitrate_kbps: int
    loudness_target_lufs: float
    is_system: bool

    @classmethod
    def of(cls, p: RenderPresetRow) -> "PresetOut":
        return cls(
            key=p.key,
            width=p.width,
            height=p.height,
            fps=p.fps,
            container=p.container,
            video_codec=p.video_codec,
            audio_codec=p.audio_codec,
            crf=p.crf,
            audio_bitrate_kbps=p.audio_bitrate_kbps,
            loudness_target_lufs=float(p.loudness_target_lufs),
            is_system=p.is_system,
        )


class RenderJobOut(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    clip_version_id: uuid.UUID
    preset_key: str
    status: str
    progress: int
    output_asset_id: uuid.UUID | None
    error: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, view: RenderView) -> "RenderJobOut":
        j = view.job
        return cls(
            id=j.id,
            clip_id=j.clip_id,
            clip_version_id=j.clip_version_id,
            preset_key=view.preset_key,
            status=j.status,
            progress=j.progress,
            output_asset_id=j.output_asset_id,
            error=j.error,
            started_at=j.started_at,
            finished_at=j.finished_at,
            created_at=j.created_at,
        )


class RenderJobPage(BaseModel):
    items: list[RenderJobOut]
    next_cursor: uuid.UUID | None


@render_router.get("/render-presets", response_model=list[PresetOut])
async def list_presets(principal: CurrentPrincipal, renders: Renders) -> list[PresetOut]:
    principal.require(READ)
    return [PresetOut.of(p) for p in await renders.list_presets(principal.org_id)]


@render_router.post(
    "/clips/{clip_id}/render", status_code=status.HTTP_202_ACCEPTED, response_model=RenderJobOut
)
async def start_render(
    clip_id: uuid.UUID, body: RenderRequest, principal: CurrentPrincipal, renders: Renders
) -> RenderJobOut:
    principal.require(WRITE)
    view, _created = await renders.start_render(principal, clip_id, preset_key=body.preset_key)
    return RenderJobOut.of(view)


@render_router.get("/render-jobs", response_model=RenderJobPage)
async def list_render_jobs(
    principal: CurrentPrincipal,
    renders: Renders,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: uuid.UUID | None = None,
    clip_id: uuid.UUID | None = None,
    job_status: Annotated[
        Literal["queued", "running", "succeeded", "failed", "canceled"] | None,
        Query(alias="status"),
    ] = None,
) -> RenderJobPage:
    principal.require(READ)
    page = await renders.list(
        principal.org_id, limit=limit, cursor=cursor, clip_id=clip_id, status=job_status
    )
    return RenderJobPage(
        items=[RenderJobOut.of(v) for v in page.items], next_cursor=page.next_cursor
    )


@render_router.get("/render-jobs/{render_job_id}", response_model=RenderJobOut)
async def get_render_job(
    render_job_id: uuid.UUID, principal: CurrentPrincipal, renders: Renders
) -> RenderJobOut:
    principal.require(READ)
    return RenderJobOut.of(await renders.get(principal.org_id, render_job_id))


@render_router.post("/render-jobs/{render_job_id}/cancel", response_model=RenderJobOut)
async def cancel_render_job(
    render_job_id: uuid.UUID, principal: CurrentPrincipal, renders: Renders
) -> RenderJobOut:
    principal.require(WRITE)
    return RenderJobOut.of(await renders.cancel(principal, render_job_id))
