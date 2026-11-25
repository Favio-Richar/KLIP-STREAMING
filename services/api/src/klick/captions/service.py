"""Casos de uso de subtítulos manuales. Única capa que toca `caption_tracks` (CLAUDE.md regla 1).

El guardado de cues usa concurrencia optimista (`If-Match` = revisión), igual que el documento de
edición: dos pestañas no se pisan.
"""

import uuid
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from klick.captions.cues import Cue, CueIssue, validate_cues
from klick.captions.models import CaptionTrack
from klick.clips.models import Clip
from klick.clips.service import parse_if_match
from klick.core.audit import record_audit
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.outbox import emit
from klick.orgs.principal import Principal

MAX_TRACKS_PER_CLIP = 5


def _actor_type(principal: Principal) -> Literal["user", "api_token", "mcp"]:
    return {"api_token": "api_token", "mcp": "mcp"}.get(principal.origin, "user")  # type: ignore[return-value]


def _not_found(what: str = "Caption track") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def cue_issues_error(issues: list[CueIssue]) -> ApiError:
    return ApiError(
        422,
        "invalid_cues",
        "Invalid request",
        "Los subtítulos no son válidos.",
        errors=[i.model_dump() for i in issues],
    )


class CaptionService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _clip_exists(
        self, s: AsyncSession, org_id: uuid.UUID, clip_id: uuid.UUID, *, lock: bool = False
    ) -> None:
        stmt = select(Clip.id).where(
            Clip.id == clip_id, Clip.org_id == org_id, Clip.deleted_at.is_(None)
        )
        if lock:
            stmt = stmt.with_for_update()  # serializa la creación de pistas del mismo clip
        found = (await s.execute(stmt)).scalar_one_or_none()
        if found is None:
            raise _not_found("Clip")

    async def _audit(
        self,
        s: AsyncSession,
        principal: Principal,
        action: str,
        track_id: uuid.UUID,
        diff: dict[str, Any],
    ) -> None:
        await record_audit(
            s,
            action=action,
            actor_type=_actor_type(principal),
            actor_id=principal.user_id,
            org_id=principal.org_id,
            resource_type="caption_track",
            resource_id=track_id,
            diff=diff,  # solo cantidades: el texto de los subtítulos no va a auditoría
        )
        await emit(
            s,
            type=action,
            org_id=principal.org_id,
            aggregate_type="caption_track",
            aggregate_id=track_id,
            payload={"id": str(track_id), **diff},
        )

    # ------------------------------------------------------------------------------ crear

    async def create_for_clip(
        self, principal: Principal, clip_id: uuid.UUID, *, language: str = "es"
    ) -> CaptionTrack:
        org_id = principal.org_id
        async with self._db.tenant_session(org_id, principal.user_id) as s:
            await self._clip_exists(s, org_id, clip_id, lock=True)
            count = (
                await s.execute(
                    select(func.count())
                    .select_from(CaptionTrack)
                    .where(CaptionTrack.clip_id == clip_id)
                )
            ).scalar_one()
            if count >= MAX_TRACKS_PER_CLIP:
                raise ApiError(
                    409,
                    "too_many_caption_tracks",
                    "Conflict",
                    f"Un clip admite hasta {MAX_TRACKS_PER_CLIP} pistas de subtítulos.",
                )
            track = CaptionTrack(
                id=new_id(),
                org_id=org_id,
                clip_id=clip_id,
                language=language,
                source="manual",
                cues=[],
                updated_by=principal.user_id,
            )
            s.add(track)
            await s.flush()
            await self._audit(s, principal, "caption.created", track.id, {"clip_id": str(clip_id)})
            return track

    # ---------------------------------------------------------------------------- consultar

    async def get(self, org_id: uuid.UUID, track_id: uuid.UUID) -> CaptionTrack:
        async with self._db.tenant_session(org_id) as s:
            track = (
                await s.execute(
                    select(CaptionTrack).where(
                        CaptionTrack.id == track_id, CaptionTrack.org_id == org_id
                    )
                )
            ).scalar_one_or_none()
            if track is None:
                raise _not_found()
            if track.clip_id is not None:  # la pista de un clip borrado ya no es accesible
                await self._clip_exists(s, org_id, track.clip_id)
            return track

    async def list_for_clip(self, org_id: uuid.UUID, clip_id: uuid.UUID) -> list[CaptionTrack]:
        async with self._db.tenant_session(org_id) as s:
            await self._clip_exists(s, org_id, clip_id)
            rows = (
                await s.execute(
                    select(CaptionTrack)
                    .where(CaptionTrack.clip_id == clip_id, CaptionTrack.org_id == org_id)
                    .order_by(CaptionTrack.id)
                )
            ).scalars()
            return list(rows)

    # -------------------------------------------------------------------------------- editar

    async def replace_cues(
        self,
        principal: Principal,
        track_id: uuid.UUID,
        cues: list[Cue],
        if_match: str | None,
    ) -> CaptionTrack:
        """Reemplaza todos los cues si la revisión esperada es la vigente; si no, 412."""
        org_id = principal.org_id
        await self.get(org_id, track_id)  # 404 antes que cualquier otro error (sin filtrar datos)
        expected = parse_if_match(if_match)
        issues = validate_cues(cues)
        if issues:
            raise cue_issues_error(issues)
        payload = [c.model_dump(exclude_none=True) for c in cues]
        async with self._db.tenant_session(org_id, principal.user_id) as s:
            row = (
                await s.execute(
                    update(CaptionTrack)
                    .where(
                        CaptionTrack.id == track_id,
                        CaptionTrack.org_id == org_id,
                        CaptionTrack.revision == expected,
                    )
                    .values(
                        cues=payload,
                        revision=CaptionTrack.revision + 1,
                        updated_by=principal.user_id,
                    )
                    .returning(CaptionTrack)
                )
            ).scalar_one_or_none()
            if row is None:
                current = (
                    await s.execute(
                        select(CaptionTrack.revision).where(CaptionTrack.id == track_id)
                    )
                ).scalar_one()
                raise ApiError(
                    412,
                    "revision_mismatch",
                    "Precondition failed",
                    "Otra persona o pestaña guardó los subtítulos antes que tú. Recarga.",
                    headers={"ETag": f'"{current}"'},
                )
            await self._audit(
                s,
                principal,
                "caption.updated",
                track_id,
                {"cues": len(payload), "revision": row.revision},
            )
            return row
