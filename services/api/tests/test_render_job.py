"""Job `render` de extremo a extremo (F1-09): API → worker real → FFmpeg real → asset exportado.

Corre en la imagen `worker-media` (`pnpm test:media`) y en CI. Incluye los dos criterios de
aceptación de robustez: cancelar a mitad de un render y que la caída del worker a mitad de un
render no pierda ni duplique el trabajo.
"""

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from actors import Actor, Users, create_org
from harness import ApiHarness
from klick.core.scheduler import reconcile_jobs
from klick.core.worker import WorkerDeps, execute_job
from klick.media.service import MediaService
from klick.storage.service import StorageService
from media_helpers import make_samples, sql
from test_media_probe import _run as run_probe
from test_media_probe import _upload

pytestmark = [
    pytest.mark.db,
    pytest.mark.ffmpeg,
    pytest.mark.skipif(
        not os.environ.get("KLICK_REQUIRE_FFMPEG"), reason="requiere FFmpeg real: pnpm test:media"
    ),
]


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("render-job-samples")
    files = make_samples(d)
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",  # noqa: S607
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25:duration=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "35",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(d / "long.mp4"),
        ],  # fmt: skip
        check=True,
    )
    files["long.mp4"] = d / "long.mp4"
    return files


@pytest.fixture
def scratch(api: ApiHarness, tmp_path: Path) -> Path:
    api.settings.media_scratch_dir = str(tmp_path)
    return tmp_path


def deps(api: ApiHarness) -> WorkerDeps:
    return WorkerDeps(
        db=api.app.state.db,
        redis=api.redis,
        settings=api.settings.model_copy(update={"job_backoff_base_s": 0.01}),
        dispatcher=api.dispatcher,
        worker_id="w-render",
    )


async def _ready_video(
    api: ApiHarness, users: Users, samples: dict[str, Path], name: str = "video.mp4"
) -> tuple[Actor, str, str]:
    ana = await users.make("ana@example.com")
    org = (await create_org(ana, "Org render"))["id"]
    asset = await _upload(ana, org, samples[name], "video/mp4")
    assert await run_probe(api, org, asset["id"]) == "succeeded"
    return ana, org, asset["id"]


async def _clip(ana: Actor, org: str, asset: str, start: int, end: int) -> str:
    r = await ana.call(
        "POST", "/clips", org=org, json={"source_asset_id": asset, "start_ms": start, "end_ms": end}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _request(ana: Actor, org: str, clip: str, **body: Any) -> dict[str, Any]:
    r = await ana.call("POST", f"/clips/{clip}/render", org=org, json=body)
    assert r.status_code == 202, r.text
    data: dict[str, Any] = r.json()
    return data


async def _job_id(api: ApiHarness, render_job_id: str) -> uuid.UUID:
    rows = await sql(
        api,
        "select id from jobs where kind = 'render' and payload->>'render_job_id' = $1 order by created_at desc limit 1",
        render_job_id,
    )
    return rows[0]["id"]  # type: ignore[no-any-return]


async def _execute(api: ApiHarness, org: str, render_job_id: str) -> str:
    return await execute_job(deps(api), await _job_id(api, render_job_id), uuid.UUID(org))


async def _state(api: ApiHarness, render_job_id: str) -> dict[str, Any]:
    row = (await sql(api, "select * from render_jobs where id = $1", uuid.UUID(render_job_id)))[0]
    return dict(row)


# ------------------------------------------------------------------------------ caso feliz


async def test_render_produces_an_analyzed_downloadable_video_billed_once(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org, asset = await _ready_video(api, users, samples)
    clip = await _clip(ana, org, asset, 500, 2500)  # 2 s
    job = await _request(ana, org, clip)

    assert await _execute(api, org, job["id"]) == "succeeded"
    state = await _state(api, job["id"])
    assert (state["status"], state["progress"], state["error"]) == ("succeeded", 100, None)
    assert state["ffmpeg_version"] == "7.1.1" and len(state["ffmpeg_args_hash"]) == 64
    assert state["started_at"] is not None and state["finished_at"] is not None
    assert not list(scratch.iterdir()), "el directorio temporal del job debe quedar vacío"

    # el video exportado es un asset más de la biblioteca: se analiza igual que una subida
    out_id = str(state["output_asset_id"])
    pending = (await ana.call("GET", f"/assets/{out_id}", org=org)).json()
    assert (pending["status"], pending["kind"], pending["source_type"]) == (
        "processing",
        "video",
        "render",
    )
    assert pending["title"].endswith("(9x16_1080_30)")
    assert await run_probe(api, org, out_id) == "succeeded"
    detail = (await ana.call("GET", f"/assets/{out_id}", org=org)).json()
    assert detail["status"] == "ready"
    assert (detail["width"], detail["height"], detail["fps"]) == (1080, 1920, 30.0)
    assert (detail["video_codec"], detail["audio_codec"]) == ("h264", "aac")
    assert abs(detail["duration_ms"] - 2000) < 150
    derivs = (await ana.call("GET", f"/assets/{out_id}/derivatives", org=org)).json()
    assert {d["kind"] for d in derivs} == {
        "proxy_720p",
        "thumbnail",
        "sprite",
        "waveform",
        "audio_16k",
    }

    # descarga firmada: es un MP4 de verdad
    url = (await ana.call("GET", f"/assets/{out_id}/download-url", org=org)).json()["url"]
    async with httpx.AsyncClient(timeout=60) as http:
        head = (await http.get(url, headers={"Range": "bytes=0-31"})).content
    assert b"ftyp" in head

    # consumo: una sola vez, exactamente los segundos renderizados
    usage = await sql(
        api,
        "select metric, quantity, unit, source_type, idempotency_key from usage_records where org_id = $1",
        uuid.UUID(org),
    )
    assert [(u["metric"], float(u["quantity"]), u["unit"], u["source_type"]) for u in usage] == [
        ("render_s", 2.0, "s", "render_job"),
    ]
    assert usage[0]["idempotency_key"] == f"render:{job['id']}"
    clip_status = (await ana.call("GET", f"/clips/{clip}", org=org)).json()["status"]
    assert clip_status == "ready"  # lo fija el render, no el usuario

    # idempotencia: pedirlo de nuevo devuelve el mismo trabajo ya terminado y no encola nada
    again = await _request(ana, org, clip)
    assert again["id"] == job["id"] and again["status"] == "succeeded"
    assert again["output_asset_id"] == out_id
    n = (
        await sql(
            api,
            "select count(*) as n from jobs where kind = 'render' and org_id = $1",
            uuid.UUID(org),
        )
    )[0]["n"]
    assert n == 1
    # un job ya terminado que se reentregara al worker se omite (el marco de jobs lo reclama una vez)
    assert await _execute(api, org, job["id"]) == "skipped"
    # y un job NUEVO para el mismo render (p. ej. del reconciliador) tampoco rehace nada ni cobra
    async with api.app.state.db.tenant_session(uuid.UUID(org)) as s:
        extra, _ = await api.app.state.job_service.enqueue(
            s, kind="render", payload={"render_job_id": job["id"]}, org_id=uuid.UUID(org)
        )
    assert await execute_job(deps(api), extra.id, uuid.UUID(org)) == "succeeded"
    result = (await sql(api, "select result::text as r from jobs where id = $1", extra.id))[0]["r"]
    assert "already_succeeded" in result
    assert (await sql(api, "select count(*) as n from usage_records"))[0]["n"] == 1


async def test_progress_events_are_published_while_rendering(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org, asset = await _ready_video(api, users, samples)
    job = await _request(ana, org, await _clip(ana, org, asset, 0, 3000))
    assert await _execute(api, org, job["id"]) == "succeeded"
    from klick.core.events import org_stream  # noqa: PLC0415

    entries = await api.redis.xrange(org_stream(uuid.UUID(org)))
    types = [
        dict(fields or {}).get("type") for _, fields in entries or []
    ]  # SSE de la organización
    assert "render.progress" in types and "job.progress" in types
    events = {
        r["type"]
        for r in await sql(api, "select type from outbox_events where org_id = $1", uuid.UUID(org))
    }
    assert {"render.requested", "render.succeeded"} <= events


# -------------------------------------------------------------------------------- fallos


async def test_unavailable_sources_fail_permanently_with_a_stable_code(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org, asset = await _ready_video(api, users, samples)
    job = await _request(ana, org, await _clip(ana, org, asset, 0, 2000))
    await sql(api, "update media_assets set deleted_at = now() where id = $1", uuid.UUID(asset))
    assert await _execute(api, org, job["id"]) == "failed"  # permanente: sin reintentos
    state = await _state(api, job["id"])
    assert state["status"] == "failed" and state["finished_at"] is not None
    assert json.loads(state["error"]) == {"code": "source_unavailable"}
    assert (await sql(api, "select attempts from jobs where kind = 'render'"))[0]["attempts"] == 1
    assert (await sql(api, "select count(*) as n from usage_records"))[0]["n"] == 0  # no se cobra
    assert not list(scratch.iterdir())
    # un fallo se puede reintentar pidiéndolo otra vez (reutiliza la fila)
    await sql(api, "update media_assets set deleted_at = null where id = $1", uuid.UUID(asset))
    retry = await _request(ana, org, str((await _state(api, job["id"]))["clip_id"]))
    assert retry["id"] == job["id"] and retry["status"] == "queued"
    assert await _execute(api, org, job["id"]) == "succeeded"


async def test_transient_failures_retry_and_the_last_attempt_fails_the_render(
    api: ApiHarness,
    users: Users,
    samples: dict[str, Path],
    scratch: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ana, org, asset = await _ready_video(api, users, samples)
    job = await _request(ana, org, await _clip(ana, org, asset, 0, 2000))

    async def broken(self: StorageService, *a: Any, **k: Any) -> int:
        raise OSError("almacén caído")

    monkeypatch.setattr(StorageService, "download", broken)
    assert await _execute(api, org, job["id"]) == "retrying"
    assert (await _state(api, job["id"]))["status"] == "queued"  # sigue en espera, no "running"
    for expected in ("retrying", "failed"):
        await sql(api, "update jobs set run_after = now() where kind = 'render'")
        assert await _execute(api, org, job["id"]) == expected
    state = await _state(api, job["id"])
    assert state["status"] == "failed" and json.loads(state["error"]) == {"code": "render_failed"}
    assert not list(scratch.iterdir())


# -------------------------------------------------------------- cancelar y caída del worker


async def _wait_progress(
    api: ApiHarness, render_job_id: str, at_least: int, limit_s: float = 90
) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while asyncio.get_running_loop().time() < deadline:
        state = await _state(api, render_job_id)
        if state["status"] == "running" and state["progress"] >= at_least:
            return
        assert state["status"] in ("queued", "running"), state
        await asyncio.sleep(0.3)
    raise AssertionError(f"el render no llegó al {at_least} % a tiempo")


async def test_cancel_while_rendering_kills_ffmpeg_and_cleans_up(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org, asset = await _ready_video(api, users, samples, "long.mp4")
    job = await _request(ana, org, await _clip(ana, org, asset, 0, 30_000))
    task = asyncio.create_task(_execute(api, org, job["id"]))
    await _wait_progress(api, job["id"], 18)

    canceled = await ana.call("POST", f"/render-jobs/{job['id']}/cancel", org=org)
    assert canceled.status_code == 200 and canceled.json()["status"] == "canceled"
    assert (
        await asyncio.wait_for(task, timeout=30) == "canceled"
    )  # FFmpeg murió: el job terminó pronto
    state = await _state(api, job["id"])
    assert state["status"] == "canceled" and state["output_asset_id"] is None
    assert (await sql(api, "select count(*) as n from usage_records"))[0]["n"] == 0
    assert (await sql(api, "select count(*) as n from media_assets where source_type = 'render'"))[
        0
    ]["n"] == 0
    assert not list(scratch.iterdir())


async def test_a_worker_crash_mid_render_loses_nothing_and_duplicates_nothing(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    """Criterio de aceptación F1: reiniciar el worker a mitad de un render no pierde el trabajo."""
    ana, org, asset = await _ready_video(api, users, samples, "long.mp4")
    job = await _request(ana, org, await _clip(ana, org, asset, 0, 30_000))

    task = asyncio.create_task(_execute(api, org, job["id"]))
    await _wait_progress(api, job["id"], 18)
    task.cancel()  # el proceso muere: nadie actualiza el estado
    with pytest.raises(asyncio.CancelledError):
        await task
    crashed = await _state(api, job["id"])
    assert crashed["status"] == "running" and crashed["output_asset_id"] is None  # quedó "a medias"
    assert (await sql(api, "select status from jobs where kind = 'render'"))[0][
        "status"
    ] == "running"

    # el reconciliador detecta el latido perdido y reencola el trabajo
    await sql(
        api, "update jobs set heartbeat_at = now() - interval '5 minutes' where kind = 'render'"
    )
    await reconcile_jobs(api.app.state.db, api.dispatcher, deps(api).settings)
    requeued = (await sql(api, "select status, attempts from jobs where kind = 'render'"))[0]
    assert requeued["status"] in ("retrying", "queued")
    await sql(api, "update jobs set run_after = now() where kind = 'render'")

    assert await _execute(api, org, job["id"]) == "succeeded"
    done = await _state(api, job["id"])
    assert done["status"] == "succeeded" and done["progress"] == 100
    assert (await sql(api, "select attempts from jobs where kind = 'render'"))[0]["attempts"] == 2
    # exactamente un resultado, un cobro y un objeto de salida
    assert (await sql(api, "select count(*) as n from media_assets where source_type = 'render'"))[
        0
    ]["n"] == 1
    usage = await sql(api, "select quantity from usage_records")
    assert [float(u["quantity"]) for u in usage] == [30.0]
    keys = await sql(api, "select key from storage_objects where key like '%/renders/%'")
    assert len(keys) == 1
    assert not list(scratch.iterdir())


async def test_a_crash_after_uploading_but_before_committing_is_idempotent(
    api: ApiHarness,
    users: Users,
    samples: dict[str, Path],
    scratch: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ana, org, asset = await _ready_video(api, users, samples)
    job = await _request(ana, org, await _clip(ana, org, asset, 0, 2000))
    original = MediaService.create_render_asset
    calls = {"n": 0}

    async def flaky(self: MediaService, *a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("caída justo después de subir el archivo")
        return await original(self, *a, **k)

    monkeypatch.setattr(MediaService, "create_render_asset", flaky)
    assert await _execute(api, org, job["id"]) == "retrying"
    assert (await _state(api, job["id"]))["status"] == "queued"
    await sql(api, "update jobs set run_after = now() where kind = 'render'")
    assert await _execute(api, org, job["id"]) == "succeeded"
    assert (await sql(api, "select count(*) as n from media_assets where source_type = 'render'"))[
        0
    ]["n"] == 1
    assert (await sql(api, "select count(*) as n from usage_records"))[0]["n"] == 1
    assert len(await sql(api, "select key from storage_objects where key like '%/renders/%'")) == 1
