"""API de subtítulos (docs/05 §2). Permisos `clips:read|write`.

`PUT /caption-tracks/{id}/cues` reemplaza todos los cues y exige `If-Match: "<revision>"`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from klick.captions.cues import MAX_CUES, Cue
from klick.captions.models import CaptionTrack
from klick.captions.service import CaptionService
from klick.orgs.deps import CurrentPrincipal

captions_router = APIRouter(tags=["captions"])
READ, WRITE = "clips:read", "clips:write"


def get_caption_service(request: Request) -> CaptionService:
    service: CaptionService = request.app.state.caption_service
    return service


Captions = Annotated[CaptionService, Depends(get_caption_service)]


class TrackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str = Field(default="es", pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class CuesReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cues: Annotated[list[Cue], Field(max_length=MAX_CUES + 1)]


class TrackOut(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID | None
    language: str
    source: str
    status: str
    revision: int
    cues: list[dict[str, Any]]
    updated_at: datetime | None

    @classmethod
    def of(cls, t: CaptionTrack) -> "TrackOut":
        return cls(
            id=t.id,
            clip_id=t.clip_id,
            language=t.language,
            source=t.source,
            status=t.status,
            revision=t.revision,
            cues=t.cues,
            updated_at=t.updated_at,
        )


def _etag(revision: int) -> str:
    return f'"{revision}"'


@captions_router.post(
    "/clips/{clip_id}/caption-tracks", status_code=status.HTTP_201_CREATED, response_model=TrackOut
)
async def create_track(
    clip_id: uuid.UUID,
    body: TrackCreate,
    response: Response,
    principal: CurrentPrincipal,
    captions: Captions,
) -> TrackOut:
    principal.require(WRITE)
    track = await captions.create_for_clip(principal, clip_id, language=body.language)
    response.headers["ETag"] = _etag(track.revision)
    return TrackOut.of(track)


@captions_router.get("/clips/{clip_id}/caption-tracks", response_model=list[TrackOut])
async def list_tracks(
    clip_id: uuid.UUID, principal: CurrentPrincipal, captions: Captions
) -> list[TrackOut]:
    principal.require(READ)
    return [TrackOut.of(t) for t in await captions.list_for_clip(principal.org_id, clip_id)]


@captions_router.get("/caption-tracks/{track_id}", response_model=TrackOut)
async def get_track(
    track_id: uuid.UUID, response: Response, principal: CurrentPrincipal, captions: Captions
) -> TrackOut:
    principal.require(READ)
    track = await captions.get(principal.org_id, track_id)
    response.headers["ETag"] = _etag(track.revision)
    return TrackOut.of(track)


@captions_router.put("/caption-tracks/{track_id}/cues", response_model=TrackOut)
async def replace_cues(
    track_id: uuid.UUID,
    body: CuesReplace,
    response: Response,
    principal: CurrentPrincipal,
    captions: Captions,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> TrackOut:
    principal.require(WRITE)
    track = await captions.replace_cues(principal, track_id, body.cues, if_match)
    response.headers["ETag"] = _etag(track.revision)
    return TrackOut.of(track)
