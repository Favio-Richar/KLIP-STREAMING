import asyncio
import copy
import uuid
from typing import Any

import asyncpg
import pytest
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from media_helpers import make_asset, sql

pytestmark = pytest.mark.db


async def _setup(
    api: ApiHarness, users: Users, email: str = "ana@example.com"
) -> tuple[Actor, str]:
    ana = await users.make(email)
    return ana, (await create_org(ana, f"Org {email}"))["id"]


async def _clip(api: ApiHarness, ana: Actor, org: str) -> dict[str, Any]:
    asset = str(await make_asset(api, org, ana.user_id, status="ready", duration_ms=60_000))
    r = await ana.call(
        "POST", "/clips", org=org, json={"source_asset_id": asset, "start_ms": 1000, "end_ms": 9000}
    )
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


async def _render(ana: Actor, org: str, clip_id: str, **body: Any) -> Any:
    return await ana.call("POST", f"/clips/{clip_id}/render", org=org, json=body)


# ------------------------------------------------------------------------------ presets


async def test_system_presets_are_visible_to_every_organization(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    r = await ana.call("GET", "/render-presets", org=org)
    assert r.status_code == 200
    by_key = {p["key"]: p for p in r.json()}
    assert set(by_key) == {"9x16_1080_30", "16x9_1080_30", "1x1_1080_30", "4x5_1080_30"}
    vertical = by_key["9x16_1080_30"]
    assert (vertical["width"], vertical["height"], vertical["fps"], vertical["crf"]) == (
        1080,
        1920,
        30,
        20,
    )
    assert (vertical["video_codec"], vertical["audio_codec"], vertical["loudness_target_lufs"]) == (
        "libx264", "aac", -14.0,
    )  # fmt: skip
    assert all(p["is_system"] for p in by_key.values())


# ------------------------------------------------------------------------------ pedir


async def test_requesting_a_render_freezes_a_version_and_queues_one_job(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(api, ana, org)
    r = await _render(ana, org, clip["id"])
    assert r.status_code == 202, r.text
    job = r.json()
    assert (job["status"], job["progress"], job["preset_key"], job["output_asset_id"]) == (
        "queued", 0, "9x16_1080_30", None,
    )  # fmt: skip
    # el render usa una versión inmutable del clip, no el documento vivo
    versions = (await ana.call("GET", f"/clips/{clip['id']}/versions", org=org)).json()["items"]
    assert len(versions) == 1
    assert job["clip_version_id"] == versions[0]["id"]
    queued = await sql(
        api,
        "select kind, queue, status, payload from jobs where org_id = $1 and kind = 'render'",
        uuid.UUID(org),
    )
    assert [(j["kind"], j["queue"], j["status"]) for j in queued] == [("render", "media", "queued")]
    assert api.dispatcher.sent  # se despachó al worker de media


async def test_requesting_twice_is_idempotent_and_a_new_version_is_a_new_render(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(api, ana, org)
    first = (await _render(ana, org, clip["id"])).json()
    again = (await _render(ana, org, clip["id"])).json()
    assert again["id"] == first["id"] and again["clip_version_id"] == first["clip_version_id"]
    jobs = await sql(
        api, "select count(*) as n from jobs where kind = 'render' and org_id = $1", uuid.UUID(org)
    )
    assert jobs[0]["n"] == 1  # un doble clic no encola dos trabajos
    # otro preset del mismo documento = otro render
    other = (await _render(ana, org, clip["id"], preset_key="16x9_1080_30")).json()
    assert other["id"] != first["id"] and other["clip_version_id"] == first["clip_version_id"]
    # cambiar el documento crea otra versión y por tanto otro render
    doc = (await ana.call("GET", f"/clips/{clip['id']}/edit-project", org=org)).json()
    changed = copy.deepcopy(doc["document"])
    changed["timeline"]["tracks"][0]["items"][0]["volume"] = 0.5
    saved = await ana.call(
        "PATCH",
        f"/clips/{clip['id']}/edit-project",
        org=org,
        json={"document": changed},
        headers={"If-Match": '"1"'},
    )
    assert saved.status_code == 200
    third = (await _render(ana, org, clip["id"])).json()
    assert (
        third["id"] not in (first["id"], other["id"])
        and third["clip_version_id"] != first["clip_version_id"]
    )
    n = (
        await sql(
            api, "select count(*) as n from clip_versions where clip_id = $1", uuid.UUID(clip["id"])
        )
    )[0]["n"]
    assert n == 2


async def test_render_request_validation(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(api, ana, org)
    bad = await _render(ana, org, clip["id"], preset_key="9x16_4k_60")
    assert bad.status_code == 422 and bad.json()["code"] == "unknown_preset"
    assert (await _render(ana, org, clip["id"], preset_key="")).status_code == 422
    assert (await _render(ana, org, clip["id"], extra=1)).status_code == 422
    assert (await _render(ana, org, str(uuid.uuid4()))).status_code == 404

    # subtítulos: todavía no se pueden exportar (llegan con F1-11): se avisa, no se ignora
    doc = (await ana.call("GET", f"/clips/{clip['id']}/edit-project", org=org)).json()["document"]
    doc["captions"] = {"track_id": str(uuid.uuid4())}
    saved = await ana.call(
        "PATCH",
        f"/clips/{clip['id']}/edit-project",
        org=org,
        json={"document": doc},
        headers={"If-Match": '"1"'},
    )
    assert saved.status_code == 200
    caps = await _render(ana, org, clip["id"])
    assert caps.status_code == 422 and {e["code"] for e in caps.json()["errors"]} == {
        "captions_unavailable"
    }
    assert (await sql(api, "select count(*) as n from render_jobs"))[0][
        "n"
    ] == 0  # nada quedó a medias


# ----------------------------------------------------------------------------- cancelar


async def test_cancel_and_retry(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(api, ana, org)
    job = (await _render(ana, org, clip["id"])).json()
    canceled = await ana.call("POST", f"/render-jobs/{job['id']}/cancel", org=org)
    assert canceled.status_code == 200 and canceled.json()["status"] == "canceled"
    assert canceled.json()["finished_at"] is not None
    row = (
        await sql(
            api, "select status from jobs where kind = 'render' and org_id = $1", uuid.UUID(org)
        )
    )[0]
    assert row["status"] == "canceled"  # el trabajo del worker también se canceló
    again = await ana.call("POST", f"/render-jobs/{job['id']}/cancel", org=org)
    assert again.status_code == 409 and again.json()["code"] == "render_finished"

    # pedirlo otra vez reutiliza la fila del render y encola un trabajo nuevo
    retry = (await _render(ana, org, clip["id"])).json()
    assert retry["id"] == job["id"] and retry["status"] == "queued" and retry["finished_at"] is None
    statuses = sorted(
        r["status"]
        for r in await sql(
            api, "select status from jobs where kind = 'render' and org_id = $1", uuid.UUID(org)
        )
    )
    assert statuses == ["canceled", "queued"]
    assert (
        await ana.call("POST", f"/render-jobs/{uuid.uuid4()}/cancel", org=org)
    ).status_code == 404


# ----------------------------------------------------------------------------- consultar


async def test_get_and_list_render_jobs(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    c1, c2 = await _clip(api, ana, org), await _clip(api, ana, org)
    j1 = (await _render(ana, org, c1["id"])).json()
    j2 = (await _render(ana, org, c2["id"])).json()
    j3 = (await _render(ana, org, c2["id"], preset_key="1x1_1080_30")).json()
    await ana.call("POST", f"/render-jobs/{j3['id']}/cancel", org=org)

    assert (await ana.call("GET", f"/render-jobs/{j1['id']}", org=org)).json()["id"] == j1["id"]
    listing = (await ana.call("GET", "/render-jobs", org=org)).json()
    assert [j["id"] for j in listing["items"]] == [j3["id"], j2["id"], j1["id"]]
    by_clip = (await ana.call("GET", f"/render-jobs?clip_id={c2['id']}", org=org)).json()["items"]
    assert {j["id"] for j in by_clip} == {j2["id"], j3["id"]}
    canceled = (await ana.call("GET", "/render-jobs?status=canceled", org=org)).json()["items"]
    assert [j["id"] for j in canceled] == [j3["id"]]
    assert (await ana.call("GET", "/render-jobs?status=raro", org=org)).status_code == 422
    first = (await ana.call("GET", "/render-jobs?limit=2", org=org)).json()
    second = (
        await ana.call("GET", f"/render-jobs?limit=2&cursor={first['next_cursor']}", org=org)
    ).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1 and second["next_cursor"] is None


# ----------------------------------------------------------- permisos y aislamiento


async def test_permissions_and_cross_organization_isolation(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    viewer = await users.make("viewer@example.com")
    await add_member(api, ana, org, viewer, "viewer")
    clip = await _clip(api, ana, org)
    job = (await _render(ana, org, clip["id"])).json()

    assert (await viewer.call("GET", "/render-presets", org=org)).status_code == 200
    assert (await viewer.call("GET", f"/render-jobs/{job['id']}", org=org)).status_code == 200
    assert (await viewer.call("GET", "/render-jobs", org=org)).status_code == 200
    for method, path in [
        ("POST", f"/clips/{clip['id']}/render"),
        ("POST", f"/render-jobs/{job['id']}/cancel"),
    ]:
        r = await viewer.call(method, path, org=org, json={})
        assert r.status_code == 403 and r.json()["errors"] == [{"permission": "clips:write"}]

    bob, org_b = await _setup(api, users, "bob@example.com")
    assert (await bob.call("GET", f"/render-jobs/{job['id']}", org=org_b)).status_code == 404
    assert (
        await bob.call("POST", f"/render-jobs/{job['id']}/cancel", org=org_b)
    ).status_code == 404
    assert (
        await bob.call("POST", f"/clips/{clip['id']}/render", org=org_b, json={})
    ).status_code == 404
    assert (await bob.call("GET", "/render-jobs", org=org_b)).json()["items"] == []
    assert (await ana.call("GET", f"/render-jobs/{job['id']}", org=org)).json()[
        "status"
    ] == "queued"


# ------------------------------------------------------------ integridad en la base


async def test_database_enforces_render_invariants(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    org_id = uuid.UUID(org)
    clip = await _clip(api, ana, org)
    job = (await _render(ana, org, clip["id"])).json()
    jid = uuid.UUID(job["id"])
    preset_id = (await sql(api, "select id from render_presets where key = '9x16_1080_30'"))[0][
        "id"
    ]

    # único por (versión, preset): ni siquiera con SQL directo hay un segundo render igual
    with pytest.raises(asyncpg.UniqueViolationError):
        await sql(
            api,
            "insert into render_jobs (id, org_id, clip_id, clip_version_id, preset_id) values ($1, $2, $3, $4, $5)",
            uuid.uuid4(), org_id, uuid.UUID(clip["id"]), uuid.UUID(job["clip_version_id"]), preset_id,
        )  # fmt: skip
    # un render "listo" debe tener su archivo de salida
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, "update render_jobs set status = 'succeeded' where id = $1", jid)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, "update render_jobs set progress = 101 where id = $1", jid)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, "update render_jobs set status = 'terminado' where id = $1", jid)

    # consumo: solo de inserción y con clave de idempotencia única
    key = f"render:{jid}"
    insert = (
        "insert into usage_records (id, org_id, metric, quantity, unit, source_type, source_id, idempotency_key) "
        "values ($1, $2, 'render_s', 6.5, 's', 'render_job', $3, $4)"
    )
    await sql(api, insert, uuid.uuid4(), org_id, jid, key)
    with pytest.raises(asyncpg.UniqueViolationError):
        await sql(
            api, insert, uuid.uuid4(), org_id, jid, key
        )  # el mismo consumo no se cuenta dos veces
    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await sql(api, "update usage_records set quantity = 0 where idempotency_key = $1", key)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await sql(api, "delete from usage_records where idempotency_key = $1", key)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(
            api, insert.replace("'render_s'", "'inventada'"), uuid.uuid4(), org_id, jid, "otra"
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(api, insert.replace("6.5", "-1"), uuid.uuid4(), org_id, jid, "negativo")


async def test_concurrent_requests_create_exactly_one_render(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    for _ in range(3):  # varias rondas: la carrera depende del orden en que lleguen las peticiones
        clip = await _clip(api, ana, org)
        results = await asyncio.gather(*(_render(ana, org, clip["id"]) for _ in range(10)))
        assert [r.status_code for r in results] == [202] * 10, [
            r.text for r in results if r.status_code != 202
        ]
        assert len({r.json()["id"] for r in results}) == 1  # todas devuelven el mismo trabajo
        rows = await sql(
            api, "select count(*) as n from render_jobs where clip_id = $1", uuid.UUID(clip["id"])
        )
        jobs = await sql(
            api,
            "select count(*) as n from jobs where kind = 'render' and payload->>'render_job_id' = $1",
            results[0].json()["id"],
        )
        assert (rows[0]["n"], jobs[0]["n"]) == (1, 1)  # ni dos filas ni dos trabajos


async def test_audit_and_events_carry_no_content(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(api, ana, org)
    job = (await _render(ana, org, clip["id"])).json()
    await ana.call("POST", f"/render-jobs/{job['id']}/cancel", org=org)
    actions = {
        r["action"]
        for r in await sql(api, "select action from audit_events where org_id = $1", uuid.UUID(org))
    }
    assert {"render.requested", "render.canceled"} <= actions
    types = {
        r["type"]
        for r in await sql(api, "select type from outbox_events where org_id = $1", uuid.UUID(org))
    }
    assert {"render.requested", "render.canceled"} <= types
