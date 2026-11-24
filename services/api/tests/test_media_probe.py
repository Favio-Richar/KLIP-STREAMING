"""Job `media.probe` de extremo a extremo (F1-04): subida real por la API a MinIO, worker real y
FFmpeg real. Corren en la imagen `worker-media` (`pnpm test:media`) y en CI.
"""

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from klick.core.worker import WorkerDeps, execute_job
from klick.storage.service import StorageService
from media_helpers import make_samples, sql

pytestmark = [
    pytest.mark.db,
    pytest.mark.ffmpeg,
    pytest.mark.skipif(
        not os.environ.get("KLICK_REQUIRE_FFMPEG"),
        reason="requiere FFmpeg real: pnpm test:media",
    ),
]


@pytest.fixture(scope="module")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return make_samples(tmp_path_factory.mktemp("probe-samples"))


@pytest.fixture
def scratch(api: ApiHarness, tmp_path: Path) -> Path:
    api.settings.media_scratch_dir = str(tmp_path)
    return tmp_path


async def _setup(
    api: ApiHarness, users: Users, email: str = "ana@example.com"
) -> tuple[Actor, str]:
    ana = await users.make(email)
    return ana, (await create_org(ana, f"Org {email}"))["id"]


def _deps(api: ApiHarness, **overrides: Any) -> WorkerDeps:
    return WorkerDeps(
        db=api.app.state.db,
        redis=api.redis,
        settings=api.settings.model_copy(update={"job_backoff_base_s": 0.01, **overrides}),
        dispatcher=api.dispatcher,
        worker_id="w-media",
    )


async def _upload(
    ana: Actor, org: str, path: Path, content_type: str, **extra: Any
) -> dict[str, Any]:
    """Sube un archivo real por la API (inicio, PUT con URL firmada, completar). Devuelve el asset."""
    data = path.read_bytes()
    r = await ana.call(
        "POST",
        "/assets/uploads",
        org=org,
        json={
            "filename": path.name,
            "size_bytes": len(data),
            "content_type": content_type,
            "rights_confirmed": True,
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    up = r.json()["upload"]
    assert up["total_parts"] == 1, "las muestras caben en una sola parte"
    urls = await ana.call(
        "POST", f"/assets/uploads/{up['id']}/parts", org=org, json={"part_numbers": [1]}
    )
    async with httpx.AsyncClient(timeout=60) as http:
        put = await http.put(urls.json()["urls"]["1"], content=data)
    assert put.status_code == 200, put.text
    done = await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "processing"
    out: dict[str, Any] = done.json()
    return out


async def _job_for(api: ApiHarness, asset_id: str) -> uuid.UUID:
    rows = await sql(
        api,
        "select id from jobs where kind = 'media.probe' and payload->>'asset_id' = $1 "
        "order by created_at desc limit 1",
        asset_id,
    )
    return rows[0]["id"]  # type: ignore[no-any-return]


async def _run(api: ApiHarness, org: str, asset_id: str, **overrides: Any) -> str:
    return await execute_job(_deps(api, **overrides), await _job_for(api, asset_id), uuid.UUID(org))


async def _derivatives(ana: Actor, org: str, asset_id: str) -> set[str]:
    r = await ana.call("GET", f"/assets/{asset_id}/derivatives", org=org)
    assert r.status_code == 200
    return {d["kind"] for d in r.json()}


# ---------------------------------------------------------------------------- caso feliz


async def test_upload_then_probe_makes_a_video_ready_with_all_derivatives(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples["video.mp4"], "video/mp4")
    # al completar la subida el análisis ya quedó encolado, exactamente una vez
    jobs = await sql(
        api,
        "select status, queue from jobs where kind = 'media.probe' and org_id = $1",
        uuid.UUID(org),
    )
    assert [(j["status"], j["queue"]) for j in jobs] == [("queued", "media")]

    assert await _run(api, org, asset["id"]) == "succeeded"

    detail = (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()
    assert detail["status"] == "ready" and detail["error"] is None
    assert (detail["width"], detail["height"], detail["fps"]) == (320, 180, 25.0)
    assert (detail["video_codec"], detail["audio_codec"], detail["has_audio"]) == (
        "h264",
        "aac",
        True,
    )
    assert 2900 <= detail["duration_ms"] <= 3200
    assert await _derivatives(ana, org, asset["id"]) == {
        "proxy_720p", "thumbnail", "sprite", "waveform", "audio_16k",
    }  # fmt: skip

    # el hash lo verificó el servidor sobre el contenido real
    digest = hashlib.sha256(samples["video.mp4"].read_bytes()).digest()
    rows = await sql(
        api,
        "select o.sha256, o.status, o.ref_count from media_assets a "
        "join storage_objects o on o.id = a.storage_object_id where a.id = $1",
        uuid.UUID(asset["id"]),
    )
    assert (bytes(rows[0]["sha256"]), rows[0]["status"], rows[0]["ref_count"]) == (
        digest,
        "ready",
        1,
    )

    # todos los objetos (original + 5 derivados) están listos, referenciados y en el almacén
    objs = await sql(
        api,
        "select o.key, o.status, o.ref_count, o.size_bytes from storage_objects o where o.org_id = $1",
        uuid.UUID(org),
    )
    assert len(objs) == 6 and all(o["status"] == "ready" and o["ref_count"] == 1 for o in objs)
    for o in objs:
        head = await api.app.state.storage.head(o["key"])
        assert head is not None and head.size == o["size_bytes"] > 0

    job = (await sql(api, "select status, progress, result from jobs where kind = 'media.probe'"))[
        0
    ]
    assert (job["status"], job["progress"]) == ("succeeded", 100)
    assert not list(scratch.iterdir()), "el directorio temporal del job debe quedar vacío"

    # una subida posterior con el hash ya verificado se deduplica: no se vuelve a subir ni analizar
    again = await ana.call(
        "POST",
        "/assets/uploads",
        org=org,
        json={
            "filename": "copia.mp4",
            "size_bytes": samples["video.mp4"].stat().st_size,
            "content_type": "video/mp4",
            "sha256": digest.hex(),
            "rights_confirmed": True,
        },
    )
    body = again.json()
    assert body["deduplicated"] is True and body["asset"]["status"] == "ready"
    assert body["asset"]["duration_ms"] == detail["duration_ms"]


async def test_derivative_files_can_be_downloaded_and_are_valid(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples["video.mp4"], "video/mp4")
    await _run(api, org, asset["id"])
    storage: StorageService = api.app.state.storage_service
    rows = await sql(
        api,
        "select d.kind, d.storage_object_id, d.meta, o.mime from media_derivatives d "
        "join storage_objects o on o.id = d.storage_object_id where d.asset_id = $1",
        uuid.UUID(asset["id"]),
    )
    by_kind = {r["kind"]: r for r in rows}
    assert {k: v["mime"] for k, v in by_kind.items()} == {
        "proxy_720p": "video/mp4",
        "thumbnail": "image/jpeg",
        "sprite": "image/jpeg",
        "waveform": "application/json",
        "audio_16k": "audio/flac",
    }
    async with httpx.AsyncClient(timeout=60) as http:
        url = await storage.presign_download(
            uuid.UUID(org), by_kind["thumbnail"]["storage_object_id"]
        )
        jpg = (await http.get(url)).content
        assert jpg[:3] == b"\xff\xd8\xff"  # JPEG real
        url = await storage.presign_download(
            uuid.UUID(org), by_kind["waveform"]["storage_object_id"]
        )
        wave = (await http.get(url)).json()
        assert wave["version"] == 1 and wave["peaks"] and max(wave["peaks"]) <= 1


# ------------------------------------------------------------------------- otros tipos


async def test_silent_video_has_no_audio_derivatives(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples["silent.mp4"], "video/mp4")
    assert await _run(api, org, asset["id"]) == "succeeded"
    assert (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()["has_audio"] is False
    assert await _derivatives(ana, org, asset["id"]) == {"proxy_720p", "thumbnail", "sprite"}


async def test_audio_and_image_assets(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    audio = await _upload(ana, org, samples["tone.wav"], "audio/wav")
    assert audio["kind"] == "audio"
    assert await _run(api, org, audio["id"]) == "succeeded"
    assert await _derivatives(ana, org, audio["id"]) == {"waveform", "audio_16k"}
    assert (await ana.call("GET", f"/assets/{audio['id']}", org=org)).json()["status"] == "ready"

    image = await _upload(ana, org, samples["image.png"], "image/png")
    assert image["kind"] == "image"
    assert await _run(api, org, image["id"]) == "succeeded"
    detail = (await ana.call("GET", f"/assets/{image['id']}", org=org)).json()
    assert (detail["status"], detail["width"], detail["height"], detail["duration_ms"]) == (
        "ready", 64, 48, None,
    )  # fmt: skip
    assert await _derivatives(ana, org, image["id"]) == {"thumbnail"}


async def test_matroska_is_accepted(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples["video.mkv"], "video/x-matroska")
    assert await _run(api, org, asset["id"]) == "succeeded"
    detail = (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()
    assert detail["status"] == "ready" and "matroska" in detail["container"]


# --------------------------------------------------------------------- rechazos permanentes


@pytest.mark.parametrize(
    ("sample", "mime", "code"),
    [
        ("text.mp4", "video/mp4", "invalid_media"),
        ("truncated.mp4", "video/mp4", "invalid_media"),
        ("playlist.mp4", "video/mp4", "invalid_media"),  # lista HLS con URL http
        ("video.mkv", "video/mp4", "invalid_media"),  # contenido distinto al declarado
    ],
)
async def test_invalid_content_fails_permanently_with_a_stable_code(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path,
    sample: str, mime: str, code: str,
) -> None:  # fmt: skip
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples[sample], mime)
    assert await _run(api, org, asset["id"]) == "failed"  # permanente: sin reintentos
    detail = (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()
    assert detail["status"] == "failed" and detail["error"] == {"code": code}
    job = (await sql(api, "select status, attempts, error from jobs where kind = 'media.probe'"))[0]
    assert (job["status"], job["attempts"]) == ("failed", 1)
    assert not list(scratch.iterdir())
    # el mensaje de FFmpeg nunca llega al usuario ni se guarda en el asset
    assert set(detail["error"]) == {"code"}


async def test_limits_are_enforced(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    long = await _upload(ana, org, samples["video.mp4"], "video/mp4")
    assert await _run(api, org, long["id"], media_max_duration_s=1) == "failed"
    assert (await ana.call("GET", f"/assets/{long['id']}", org=org)).json()["error"] == {
        "code": "too_long"
    }
    big = await _upload(ana, org, samples["hd.mp4"], "video/mp4")
    assert await _run(api, org, big["id"], media_max_dimension=1280) == "failed"
    assert (await ana.call("GET", f"/assets/{big['id']}", org=org)).json()["error"] == {
        "code": "too_large_dimensions"
    }


# --------------------------------------------------------------------- reintentos y estados


async def test_transient_failures_retry_and_the_last_attempt_fails_the_asset(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples["silent.mp4"], "video/mp4")

    async def broken(self: StorageService, *a: Any, **k: Any) -> int:
        raise OSError("almacén caído")

    monkeypatch.setattr(StorageService, "download", broken)
    assert await _run(api, org, asset["id"]) == "retrying"
    status = (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()["status"]
    assert status == "processing"  # sigue procesando: aún quedan intentos
    await sql(api, "update jobs set run_after = now() where kind = 'media.probe'")
    assert await _run(api, org, asset["id"]) == "retrying"
    await sql(api, "update jobs set run_after = now() where kind = 'media.probe'")
    assert await _run(api, org, asset["id"]) == "failed"  # tercer y último intento
    detail = (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()
    assert detail["status"] == "failed" and detail["error"] == {"code": "processing_failed"}
    assert not list(scratch.iterdir())


async def test_reprocess_recovers_a_failed_asset_and_never_leaks_objects(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples["video.mp4"], "video/mp4")
    with monkeypatch.context() as m:

        async def broken(self: StorageService, *a: Any, **k: Any) -> int:
            raise OSError("caído")

        m.setattr(StorageService, "download", broken)
        for _ in range(3):
            await sql(api, "update jobs set run_after = now() where kind = 'media.probe'")
            await _run(api, org, asset["id"])
    assert (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()["status"] == "failed"

    r = await ana.call("POST", f"/assets/{asset['id']}/reprocess", org=org)
    assert r.status_code == 202 and r.json()["status"] == "processing" and r.json()["error"] is None
    assert await _run(api, org, asset["id"]) == "succeeded"
    assert (await ana.call("GET", f"/assets/{asset['id']}", org=org)).json()["status"] == "ready"
    assert len(await _derivatives(ana, org, asset["id"])) == 5

    # reprocesar un asset listo reemplaza los derivados: mismos objetos, sin duplicados
    before = await sql(
        api, "select count(*) as n from storage_objects where org_id = $1", uuid.UUID(org)
    )
    assert (await ana.call("POST", f"/assets/{asset['id']}/reprocess", org=org)).status_code == 202
    assert await _run(api, org, asset["id"]) == "succeeded"
    after = await sql(
        api, "select count(*) as n from storage_objects where org_id = $1", uuid.UUID(org)
    )
    assert before[0]["n"] == after[0]["n"] == 6
    refs = await sql(api, "select ref_count from storage_objects where org_id = $1", uuid.UUID(org))
    assert all(r["ref_count"] == 1 for r in refs)


async def test_probe_is_idempotent_and_skips_finished_or_missing_assets(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    asset = await _upload(ana, org, samples["silent.mp4"], "video/mp4")
    assert await _run(api, org, asset["id"]) == "succeeded"
    # un job duplicado sobre un asset ya listo no hace nada (ni rehace derivados)
    media = api.app.state.media_service
    await media.enqueue_probe(uuid.UUID(org), uuid.UUID(asset["id"]), round_key="otra-ronda")
    assert await _run(api, org, asset["id"]) == "succeeded"
    result = (
        await sql(
            api,
            "select result::text as r from jobs where kind = 'media.probe' order by created_at desc",
        )
    )[0]["r"]
    assert "status_ready" in result
    # la misma ronda no crea un segundo job
    await media.enqueue_probe(uuid.UUID(org), uuid.UUID(asset["id"]), round_key="otra-ronda")
    n = (await sql(api, "select count(*) as n from jobs where kind = 'media.probe'"))[0]["n"]
    assert n == 2

    gone = await _upload(ana, org, samples["silent.mp4"], "video/mp4")
    assert (await ana.call("DELETE", f"/assets/{gone['id']}", org=org)).status_code == 204
    assert await _run(api, org, gone["id"]) == "succeeded"
    skipped = (
        await sql(api, "select result::text as r from jobs order by created_at desc limit 1")
    )[0]["r"]
    assert "asset_missing" in skipped


async def test_probe_is_an_internal_job_registered_on_the_media_queue(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    from klick.core.jobs import JOB_KINDS  # noqa: PLC0415

    spec = JOB_KINDS["media.probe"]
    assert (spec.queue, spec.public, spec.required_permission) == ("media", False, None)
    assert spec.max_attempts == 3 and spec.timeout_s >= 4 * 3600
    # no se puede crear por la API pública: es un job interno
    r = await ana.call("POST", "/jobs", org=org, json={"kind": "media.probe", "payload": {}})
    assert r.status_code == 422 and r.json()["code"] == "unknown_job_kind"


# ----------------------------------------------------------------------- reproceso: permisos


async def test_reprocess_endpoint_permissions_and_state_rules(
    api: ApiHarness, users: Users, samples: dict[str, Path], scratch: Path
) -> None:
    ana, org = await _setup(api, users)
    viewer = await users.make("viewer@example.com")
    await add_member(api, ana, org, viewer, "viewer")
    bob, org_b = await _setup(api, users, "bob@example.com")
    asset = await _upload(ana, org, samples["silent.mp4"], "video/mp4")

    # procesándose: no se puede reprocesar
    busy = await ana.call("POST", f"/assets/{asset['id']}/reprocess", org=org)
    assert busy.status_code == 409 and busy.json()["code"] == "asset_busy"
    await _run(api, org, asset["id"])
    assert (
        await viewer.call("POST", f"/assets/{asset['id']}/reprocess", org=org)
    ).status_code == 403
    assert (
        await bob.call("POST", f"/assets/{asset['id']}/reprocess", org=org_b)
    ).status_code == 404
    assert (await ana.call("POST", f"/assets/{uuid.uuid4()}/reprocess", org=org)).status_code == 404
    actions = {
        r["action"]
        for r in await sql(api, "select action from audit_events where org_id = $1", uuid.UUID(org))
    }
    assert (await ana.call("POST", f"/assets/{asset['id']}/reprocess", org=org)).status_code == 202
    actions = {
        r["action"]
        for r in await sql(api, "select action from audit_events where org_id = $1", uuid.UUID(org))
    }
    assert {"upload.completed", "asset.reprocess"} <= actions
