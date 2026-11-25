"""Casos de uso de exportación: pedir, consultar y cancelar renders. Única capa que toca
``render_jobs`` (CLAUDE.md regla 1).

Idempotencia (docs/07 §3): un render es único por ``(versión del clip, preset)``. Pedirlo otra vez
devuelve el mismo trabajo mientras esté en curso o terminado; solo uno fallido o cancelado se
vuelve a poner en cola (reutilizando su fila). Así un doble clic o un reintento del agente nunca
renderiza dos veces ni cobra dos veces.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from klick.clips.models import Clip, ClipVersion
from klick.clips.service import ClipService, _issues_error
from klick.core.audit import record_audit
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.ids import new_id
from klick.core.jobs import JobService
from klick.core.outbox import emit
from klick.edit_project import EditProjectInvalid, parse_edit_project
from klick.edit_project.validate import Issue
from klick.media.service import Page, _constraint
from klick.orgs.principal import Principal
from klick.render.models import FINAL_RENDER_STATUSES, RenderJob, RenderPresetRow

DEFAULT_PRESET = "9x16_1080_30"


def _not_found(what: str = "Render job") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def _actor_type(principal: Principal) -> Literal["user", "api_token", "mcp"]:
    return {"api_token": "api_token", "mcp": "mcp"}.get(principal.origin, "user")  # type: ignore[return-value]


@dataclass(frozen=True)
class RenderView:
    job: RenderJob
    preset_key: str


class RenderService:
    def __init__(self, db: Database, clips: ClipService, jobs: JobService) -> None:
        self._db = db
        self._clips = clips
        self._jobs = jobs

    # --------------------------------------------------------------------------- presets

    async def list_presets(self, org_id: uuid.UUID) -> list[RenderPresetRow]:
        async with self._db.tenant_session(org_id) as s:
            rows = (
                await s.execute(
                    select(RenderPresetRow)
                    .where(or_(RenderPresetRow.org_id.is_(None), RenderPresetRow.org_id == org_id))
                    .order_by(RenderPresetRow.key)
                )
            ).scalars()
            return list(rows)

    async def _preset(self, s: AsyncSession, org_id: uuid.UUID, key: str) -> RenderPresetRow:
        row = (
            await s.execute(
                select(RenderPresetRow)
                .where(
                    RenderPresetRow.key == key,
                    or_(RenderPresetRow.org_id.is_(None), RenderPresetRow.org_id == org_id),
                )
                .order_by(RenderPresetRow.org_id.is_(None))  # el de la organización gana
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError(422, "unknown_preset", "Invalid request", f"No existe el preset {key}.")
        return row

    # ----------------------------------------------------------------------------- pedir

    async def start_render(
        self, principal: Principal, clip_id: uuid.UUID, *, preset_key: str = DEFAULT_PRESET
    ) -> tuple[RenderView, bool]:
        """Pide un render del estado actual del clip. Devuelve ``(trabajo, creado)``."""
        org_id = principal.org_id
        project = await self._clips.get_edit_project(org_id, clip_id)  # 404 si no es del clip/org
        try:
            doc = parse_edit_project(project.document)
        except EditProjectInvalid as exc:
            raise _issues_error(exc.issues) from None
        issues: list[Issue] = await self._clips.reference_issues(org_id, doc, clip_id)
        if issues:
            raise _issues_error(issues)

        async with self._db.tenant_session(org_id) as s:
            preset = await self._preset(s, org_id, preset_key)
        version = await self._clips.create_version(
            principal, clip_id, reuse_if_unchanged=True
        )  # congela el documento: el render usa SIEMPRE una versión inmutable

        try:
            return await self._enqueue(principal, clip_id, version, preset)
        except IntegrityError as exc:
            if _constraint(exc) != "uq_render_jobs__version_preset":
                raise
            # Carrera: otra petición simultánea insertó el mismo render un instante antes. La
            # restricción única lo impidió; aquí se trata como "ya existe" (idempotente).
            async with self._db.tenant_session(org_id) as s:
                winner = (
                    await s.execute(
                        select(RenderJob).where(
                            RenderJob.clip_version_id == version.id,
                            RenderJob.preset_id == preset.id,
                        )
                    )
                ).scalar_one()
            return RenderView(winner, preset.key), False

    async def _enqueue(
        self,
        principal: Principal,
        clip_id: uuid.UUID,
        version: ClipVersion,
        preset: RenderPresetRow,
    ) -> tuple[RenderView, bool]:
        org_id = principal.org_id
        async with self._db.tenant_session(org_id, principal.user_id) as s:
            existing = (
                await s.execute(
                    select(RenderJob)
                    .where(
                        RenderJob.clip_version_id == version.id, RenderJob.preset_id == preset.id
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status not in ("failed", "canceled"):
                return RenderView(existing, preset.key), False  # idempotente

            created = existing is None
            job = existing or RenderJob(
                id=new_id(),
                org_id=org_id,
                clip_id=clip_id,
                clip_version_id=version.id,
                preset_id=preset.id,
                requested_by=principal.user_id,
            )
            if created:
                s.add(job)
            else:  # reintento de uno fallido o cancelado: se reutiliza la fila
                job.status, job.progress, job.error = "queued", 0, None
                job.started_at = job.finished_at = None
                job.requested_by = principal.user_id
            await s.flush()
            queued, _ = await self._jobs.enqueue(
                s,
                kind="render",
                payload={"render_job_id": str(job.id)},
                org_id=org_id,
                created_by_type=_actor_type(principal),
                created_by=principal.user_id,
                idempotency_key=f"render:{job.id}:{new_id()}",  # una ronda nueva por intento
            )
            job.job_id = queued.id
            await record_audit(
                s,
                action="render.requested",
                actor_type=_actor_type(principal),
                actor_id=principal.user_id,
                org_id=org_id,
                resource_type="render_job",
                resource_id=job.id,
                diff={"preset": preset.key, "version": version.version, "retry": not created},
            )
            await emit(
                s,
                type="render.requested",
                org_id=org_id,
                aggregate_type="render_job",
                aggregate_id=job.id,
                payload={"id": str(job.id), "clip_id": str(clip_id), "preset": preset.key},
            )
        await self._jobs.dispatch(queued)  # si Redis falla, el reconciliador reenvía el job
        return RenderView(job, preset.key), True

    # --------------------------------------------------------------------------- consultar

    async def _view(self, s: AsyncSession, job: RenderJob) -> RenderView:
        key = (
            await s.execute(select(RenderPresetRow.key).where(RenderPresetRow.id == job.preset_id))
        ).scalar_one()
        return RenderView(job, key)

    async def get(self, org_id: uuid.UUID, render_job_id: uuid.UUID) -> RenderView:
        async with self._db.tenant_session(org_id) as s:
            job = (
                await s.execute(
                    select(RenderJob).where(
                        RenderJob.id == render_job_id, RenderJob.org_id == org_id
                    )
                )
            ).scalar_one_or_none()
            if job is None:
                raise _not_found()
            return await self._view(s, job)

    async def list(
        self,
        org_id: uuid.UUID,
        *,
        limit: int = 50,
        cursor: uuid.UUID | None = None,
        clip_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> Page[RenderView]:
        limit = max(1, min(limit, 200))
        async with self._db.tenant_session(org_id) as s:
            stmt = select(RenderJob).where(RenderJob.org_id == org_id)
            if clip_id:
                stmt = stmt.where(RenderJob.clip_id == clip_id)
            if status:
                stmt = stmt.where(RenderJob.status == status)
            if cursor:
                stmt = stmt.where(RenderJob.id < cursor)
            rows = list(
                (await s.execute(stmt.order_by(RenderJob.id.desc()).limit(limit + 1))).scalars()
            )
            views = [await self._view(s, r) for r in rows[:limit]]
        return Page(views, rows[limit - 1].id if len(rows) > limit else None)

    # ---------------------------------------------------------------------------- cancelar

    async def cancel(self, principal: Principal, render_job_id: uuid.UUID) -> RenderView:
        org_id = principal.org_id
        async with self._db.tenant_session(org_id, principal.user_id) as s:
            job = (
                await s.execute(
                    select(RenderJob)
                    .where(RenderJob.id == render_job_id, RenderJob.org_id == org_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                raise _not_found()
            if job.status in FINAL_RENDER_STATUSES:
                raise ApiError(
                    409, "render_finished", "Conflict", f"El render ya terminó ({job.status})."
                )
            job_id = job.job_id
        if job_id is not None:
            try:
                await self._jobs.cancel(org_id, job_id)  # el worker lo detecta y mata a FFmpeg
            except ApiError as exc:
                if exc.code != "job_finished":
                    raise
        async with self._db.tenant_session(org_id, principal.user_id) as s:
            job = (
                await s.execute(
                    select(RenderJob).where(RenderJob.id == render_job_id).with_for_update()
                )
            ).scalar_one()
            if job.status not in FINAL_RENDER_STATUSES:
                job.status = "canceled"
                job.finished_at = datetime.now(UTC)
            await record_audit(
                s,
                action="render.canceled",
                actor_type=_actor_type(principal),
                actor_id=principal.user_id,
                org_id=org_id,
                resource_type="render_job",
                resource_id=render_job_id,
            )
            await emit(
                s,
                type="render.canceled",
                org_id=org_id,
                aggregate_type="render_job",
                aggregate_id=render_job_id,
                payload={"id": str(render_job_id)},
            )
            return await self._view(s, job)

    # ------------------------------------------------------------------- uso por el worker

    async def load_for_worker(
        self, org_id: uuid.UUID, render_job_id: uuid.UUID
    ) -> tuple[RenderJob, ClipVersion, Clip, RenderPresetRow] | None:
        async with self._db.tenant_session(org_id) as s:
            job = (
                await s.execute(select(RenderJob).where(RenderJob.id == render_job_id))
            ).scalar_one_or_none()
            if job is None:
                return None
            version = (
                await s.execute(select(ClipVersion).where(ClipVersion.id == job.clip_version_id))
            ).scalar_one()
            clip = (await s.execute(select(Clip).where(Clip.id == job.clip_id))).scalar_one()
            preset = (
                await s.execute(select(RenderPresetRow).where(RenderPresetRow.id == job.preset_id))
            ).scalar_one()
            return job, version, clip, preset
