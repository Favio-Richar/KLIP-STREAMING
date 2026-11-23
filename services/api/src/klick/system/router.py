"""Jobs y flujo SSE de eventos (docs/05 §2: "Trabajos y eventos")."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from klick.core.audit import record_audit
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.core.events import sse_stream
from klick.core.idempotency import Idem, validate_key
from klick.core.jobs import JOB_KINDS, Job, JobService
from klick.orgs.deps import CurrentPrincipal, StreamPrincipal
from klick.orgs.principal import Principal

jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])
events_router = APIRouter(prefix="/events", tags=["events"])


def get_job_service(request: Request) -> JobService:
    service: JobService = request.app.state.job_service
    return service


Jobs = Annotated[JobService, Depends(get_job_service)]


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    delay_s: float = Field(default=0, ge=0, le=86400)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    queue: str
    status: str
    progress: int
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    correlation_id: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobPage(BaseModel):
    items: list[JobOut]
    next_cursor: uuid.UUID | None


class JobAccepted(BaseModel):
    job_id: uuid.UUID
    status_url: str
    job: JobOut


def _out(job: Job) -> dict[str, Any]:
    return JobOut.model_validate(job).model_dump(mode="json")


def _public_kind(kind: str) -> Any:
    spec = JOB_KINDS.get(kind)
    if spec is None or not spec.public:
        raise ApiError(422, "unknown_job_kind", "Unknown job kind", f"No existe el tipo {kind}.")
    return spec


@jobs_router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def create_job(
    body: JobCreate,
    request: Request,
    principal: CurrentPrincipal,
    jobs: Jobs,
    idem: Idem,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    spec = _public_kind(body.kind)
    if spec.required_permission:
        principal.require(spec.required_permission)
    key = validate_key(idempotency_key) if idempotency_key else None
    db: Database = request.app.state.db

    async def produce() -> tuple[int, Any]:
        async with db.tenant_session(principal.org_id, principal.user_id) as s:
            job, created = await jobs.enqueue(
                s,
                kind=body.kind,
                payload=body.payload,
                org_id=principal.org_id,
                created_by_type="api_token" if principal.origin == "api_token" else "user",
                created_by=principal.user_id,
                idempotency_key=key,
                delay_s=body.delay_s,
            )
            if created:
                await record_audit(
                    s,
                    action="job.created",
                    actor_type="api_token" if principal.origin == "api_token" else "user",
                    actor_id=principal.user_id,
                    org_id=principal.org_id,
                    resource_type="job",
                    resource_id=job.id,
                    diff={"kind": body.kind},
                )
        if created:
            await jobs.dispatch(job)
        out = _out(job)
        return 202, {"job_id": str(job.id), "status_url": f"/api/v1/jobs/{job.id}", "job": out}

    return await idem.run(
        request, principal.org_id, "jobs.create", body.model_dump(mode="json"), produce
    )


@jobs_router.get("", response_model=JobPage)
async def list_jobs(
    principal: CurrentPrincipal,
    jobs: Jobs,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: uuid.UUID | None = None,
    job_status: Annotated[str | None, Query(alias="status")] = None,
) -> JobPage:
    rows, next_cursor = await jobs.list(
        principal.org_id, limit=limit, cursor=cursor, status=job_status
    )
    return JobPage(items=[JobOut.model_validate(j) for j in rows], next_cursor=next_cursor)


@jobs_router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, principal: CurrentPrincipal, jobs: Jobs) -> JobOut:
    return JobOut.model_validate(await jobs.get(principal.org_id, job_id))


@jobs_router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: uuid.UUID, request: Request, principal: CurrentPrincipal, jobs: Jobs
) -> JobOut:
    job = await jobs.get(principal.org_id, job_id)
    spec = JOB_KINDS.get(job.kind)
    if spec is not None and spec.required_permission:
        principal.require(spec.required_permission)
    canceled = await jobs.cancel(principal.org_id, job_id)
    db: Database = request.app.state.db
    async with db.tenant_session(principal.org_id, principal.user_id) as s:
        await record_audit(
            s,
            action="job.canceled",
            actor_type="api_token" if principal.origin == "api_token" else "user",
            actor_id=principal.user_id,
            org_id=principal.org_id,
            resource_type="job",
            resource_id=job_id,
        )
    return JobOut.model_validate(canceled)


@events_router.get("/stream")
async def event_stream(
    request: Request,
    principal: StreamPrincipal,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Eventos en tiempo real de la organización (SSE). Reanuda con ``Last-Event-ID``."""
    return await _stream(request, principal, last_event_id)


async def _stream(
    request: Request, principal: Principal, last_event_id: str | None
) -> StreamingResponse:
    redis = request.app.state.redis

    async def body() -> Any:
        agen = sse_stream(redis, principal.org_id, last_event_id=last_event_id)
        try:
            async for frame in agen:
                if await request.is_disconnected():
                    break
                yield frame
        finally:
            await agen.aclose()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
