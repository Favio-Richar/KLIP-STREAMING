import asyncio
import copy
import uuid
from typing import Any

import asyncpg
import pytest
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from klick.clips.builder import initial_document
from klick.edit_project import parse_edit_project
from media_helpers import make_asset, media, principal, sql
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


async def _setup(
    api: ApiHarness, users: Users, email: str = "ana@example.com"
) -> tuple[Actor, str]:
    ana = await users.make(email)
    return ana, (await create_org(ana, f"Org {email}"))["id"]


async def _video(api: ApiHarness, ana: Actor, org: str, **kw: Any) -> str:
    return str(
        await make_asset(
            api,
            org,
            ana.user_id,
            title=kw.pop("title", "Directo"),
            status="ready",
            duration_ms=60_000,
            **kw,
        )
    )


async def _clip(ana: Actor, org: str, asset: str, **over: Any) -> dict[str, Any]:
    body = {"source_asset_id": asset, "start_ms": 5_000, "end_ms": 20_000, **over}
    r = await ana.call("POST", "/clips", org=org, json=body)
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


async def _doc(ana: Actor, org: str, clip_id: str) -> tuple[dict[str, Any], str]:
    r = await ana.call("GET", f"/clips/{clip_id}/edit-project", org=org)
    assert r.status_code == 200
    return r.json()["document"], r.headers["etag"]


async def _save(ana: Actor, org: str, clip_id: str, doc: dict[str, Any], etag: str | None) -> Any:
    headers = {"If-Match": etag} if etag is not None else {}
    return await ana.call(
        "PATCH", f"/clips/{clip_id}/edit-project", org=org, json={"document": doc}, headers=headers
    )


# --------------------------------------------------------------------------- pura


def test_initial_document_is_valid_for_every_aspect() -> None:
    for aspect, size in {
        "9x16": (1080, 1920),
        "16x9": (1920, 1080),
        "1x1": (1080, 1080),
        "4x5": (1080, 1350),
    }.items():
        doc = initial_document(
            asset_id=str(uuid.uuid4()), start_ms=1000, end_ms=9000, aspect=aspect
        )
        parsed = parse_edit_project(doc)
        assert (parsed.canvas.width, parsed.canvas.height) == size
        assert parsed.timeline.duration_ms == 8000


# ------------------------------------------------------------------------------ crear


async def test_create_clip_builds_the_initial_edit_project(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    asset = await _video(api, ana, org, title="Torneo de ajedrez")
    r = await ana.call(
        "POST", "/clips", org=org,
        json={"source_asset_id": asset, "start_ms": 5000, "end_ms": 20000},
    )  # fmt: skip
    assert r.status_code == 201 and r.headers["etag"] == '"1"'
    clip = r.json()
    assert (clip["title"], clip["status"], clip["revision"], clip["current_version_id"]) == (
        "Torneo de ajedrez", "draft", 1, None,
    )  # fmt: skip
    doc, etag = await _doc(ana, org, clip["id"])
    assert etag == '"1"'
    parsed = parse_edit_project(doc)
    item = parsed.timeline.tracks[0].items[0]
    assert (item.source_in_ms, item.source_out_ms, item.start_ms) == (5000, 20000, 0)  # type: ignore[union-attr]
    assert parsed.sources[0].asset_id == asset and (parsed.canvas.width, parsed.canvas.height) == (
        1080,
        1920,
    )
    assert parsed.timeline.duration_ms == 15000

    custom = await _clip(ana, org, asset, title="  Jugada final ", aspect="16x9")
    assert custom["title"] == "Jugada final"
    doc2, _ = await _doc(ana, org, custom["id"])
    assert doc2["canvas"]["width"] == 1920


@pytest.mark.parametrize(
    ("over", "status", "code"),
    [
        ({"end_ms": 60_001}, 422, "invalid_range"),  # más allá del final del video
        ({"start_ms": 20_000, "end_ms": 20_000}, 422, "invalid_range"),
        ({"start_ms": 30_000, "end_ms": 10_000}, 422, "invalid_range"),
        ({"start_ms": 1000, "end_ms": 1050}, 422, "clip_too_short"),
        ({"aspect": "3x1"}, 422, None),
        ({"title": "   "}, 422, None),
        ({"extra": 1}, 422, None),
        ({"start_ms": -1}, 422, None),
    ],
)
async def test_create_clip_validation(
    api: ApiHarness, users: Users, over: dict[str, Any], status: int, code: str | None
) -> None:
    ana, org = await _setup(api, users)
    asset = await _video(api, ana, org)
    body = {"source_asset_id": asset, "start_ms": 5000, "end_ms": 20000, **over}
    r = await ana.call("POST", "/clips", org=org, json=body)
    assert r.status_code == status, r.text
    if code:
        assert r.json()["code"] == code
    assert (await ana.call("GET", "/clips", org=org)).json()["items"] == []  # nada a medias


async def test_create_clip_source_rules(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    audio = await _video(api, ana, org, kind="audio")
    r = await ana.call(
        "POST", "/clips", org=org, json={"source_asset_id": audio, "start_ms": 0, "end_ms": 5000}
    )
    assert r.status_code == 422 and r.json()["code"] == "clip_source_not_video"
    pending = str(await make_asset(api, org, ana.user_id, status="uploading"))
    r = await ana.call(
        "POST", "/clips", org=org, json={"source_asset_id": pending, "start_ms": 0, "end_ms": 5000}
    )
    assert r.status_code == 409 and r.json()["code"] == "asset_not_ready"
    ghost = await ana.call(
        "POST",
        "/clips",
        org=org,
        json={"source_asset_id": str(uuid.uuid4()), "start_ms": 0, "end_ms": 5000},
    )
    assert ghost.status_code == 404
    video = await _video(api, ana, org)
    bad_project = await ana.call(
        "POST", "/clips", org=org,
        json={"source_asset_id": video, "start_ms": 0, "end_ms": 5000, "project_id": str(uuid.uuid4())},
    )  # fmt: skip
    assert bad_project.status_code == 404


# ------------------------------------------------------------------------------ listar


async def test_listing_filters_search_and_pagination(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    a1, a2 = await _video(api, ana, org, title="Uno"), await _video(api, ana, org, title="Dos")
    proj = (await ana.call("POST", "/projects", org=org, json={"name": "P"})).json()["id"]
    c1 = await _clip(ana, org, a1, title="Final de ajedrez", project_id=proj)
    c2 = await _clip(ana, org, a1, title="Receta de pasta")
    c3 = await _clip(ana, org, a2, title="Torneo de ajedrez")
    await ana.call("PATCH", f"/clips/{c2['id']}", org=org, json={"status": "archived"})

    def ids(resp: Any) -> list[str]:
        return [c["id"] for c in resp.json()["items"]]

    assert ids(await ana.call("GET", "/clips", org=org)) == [c3["id"], c2["id"], c1["id"]]
    assert set(ids(await ana.call("GET", f"/clips?source_asset_id={a1}", org=org))) == {
        c1["id"],
        c2["id"],
    }
    assert ids(await ana.call("GET", f"/clips?project_id={proj}", org=org)) == [c1["id"]]
    assert ids(await ana.call("GET", "/clips?status=archived", org=org)) == [c2["id"]]
    assert set(ids(await ana.call("GET", "/clips?q=ajedrez", org=org))) == {c1["id"], c3["id"]}
    assert ids(await ana.call("GET", "/clips?q=pas", org=org)) == [c2["id"]]
    assert (await ana.call("GET", "/clips?status=ready2", org=org)).status_code == 422
    first = (await ana.call("GET", "/clips?limit=2", org=org)).json()
    second = (
        await ana.call("GET", f"/clips?limit=2&cursor={first['next_cursor']}", org=org)
    ).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1 and second["next_cursor"] is None


# ---------------------------------------------------------------------------- actualizar


async def test_update_and_soft_delete(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    asset = await _video(api, ana, org)
    clip = await _clip(ana, org, asset)
    r = await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"title": " Nuevo "})
    assert r.status_code == 200 and r.json()["title"] == "Nuevo"
    assert (
        await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"status": "archived"})
    ).json()["status"] == "archived"
    assert (
        await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"status": "draft"})
    ).json()["status"] == "draft"
    # `ready` lo fija el render, no el usuario
    assert (
        await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"status": "ready"})
    ).status_code == 422
    assert (
        await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"title": ""})
    ).status_code == 422
    proj = (await ana.call("POST", "/projects", org=org, json={"name": "P"})).json()["id"]
    moved = await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"project_id": proj})
    assert moved.json()["project_id"] == proj
    out = await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"project_id": None})
    assert out.json()["project_id"] is None
    assert (
        await ana.call(
            "PATCH", f"/clips/{clip['id']}", org=org, json={"project_id": str(uuid.uuid4())}
        )
    ).status_code == 404

    # el video de origen no se puede eliminar mientras haya clips vivos hechos con él
    blocked = await ana.call("DELETE", f"/assets/{asset}", org=org)
    assert blocked.status_code == 409 and blocked.json()["code"] == "asset_in_use"
    assert (await ana.call("DELETE", f"/clips/{clip['id']}", org=org)).status_code == 204
    assert (await ana.call("GET", f"/clips/{clip['id']}", org=org)).status_code == 404
    assert (await ana.call("GET", f"/clips/{clip['id']}/edit-project", org=org)).status_code == 404
    assert (await ana.call("GET", "/clips", org=org)).json()["items"] == []
    assert (
        await ana.call("DELETE", f"/assets/{asset}", org=org)
    ).status_code == 204  # ya sin clips


# --------------------------------------------------------------- documento: concurrencia


async def test_edit_project_requires_if_match_and_advances_the_revision(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    doc, etag = await _doc(ana, org, clip["id"])

    missing = await _save(ana, org, clip["id"], doc, None)
    assert missing.status_code == 428 and missing.json()["code"] == "if_match_required"
    for bad in ("abc", '"1.5"', "*", '"-1"', '"1" "2"'):
        r = await _save(ana, org, clip["id"], doc, bad)
        assert r.status_code == 400 and r.json()["code"] == "invalid_if_match", bad

    doc["timeline"]["tracks"][0]["items"][0]["volume"] = 0.5
    ok = await _save(ana, org, clip["id"], doc, etag)
    assert ok.status_code == 200 and ok.headers["etag"] == '"2"'
    assert (
        ok.json()["revision"] == 2
        and ok.json()["document"]["timeline"]["tracks"][0]["items"][0]["volume"] == 0.5
    )
    again, new_etag = await _doc(ana, org, clip["id"])
    assert new_etag == '"2"' and again == doc
    assert (await ana.call("GET", f"/clips/{clip['id']}", org=org)).json()["revision"] == 2

    # una revisión vieja se rechaza y dice cuál es la vigente
    stale = await _save(ana, org, clip["id"], doc, etag)
    assert stale.status_code == 412 and stale.json()["code"] == "revision_mismatch"
    assert stale.headers["etag"] == '"2"'
    # formas equivalentes de la cabecera
    assert (await _save(ana, org, clip["id"], doc, "2")).status_code == 200
    assert (await _save(ana, org, clip["id"], doc, 'W/"3"')).status_code == 200


async def test_concurrent_saves_with_the_same_revision_only_one_wins(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    doc, etag = await _doc(ana, org, clip["id"])
    docs = []
    for volume in (0.2, 0.4, 0.6, 0.8):
        d = copy.deepcopy(doc)
        d["timeline"]["tracks"][0]["items"][0]["volume"] = volume
        docs.append(d)
    results = await asyncio.gather(*(_save(ana, org, clip["id"], d, etag) for d in docs))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 412, 412, 412]  # exactamente uno gana: nadie pisa a nadie
    winner = next(d for d, r in zip(docs, results, strict=True) if r.status_code == 200)
    final, final_etag = await _doc(ana, org, clip["id"])
    assert final == winner and final_etag == '"2"'


# ------------------------------------------------------------------ documento: validación


async def test_invalid_documents_are_never_saved(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    doc, etag = await _doc(ana, org, clip["id"])

    async def expect_422(mutate: Any, *codes: str) -> None:
        d = copy.deepcopy(doc)
        mutate(d)
        r = await _save(ana, org, clip["id"], d, etag)
        assert r.status_code == 422 and r.json()["code"] == "invalid_edit_project", r.text
        got = {e["code"] for e in r.json()["errors"]}
        assert got == set(codes), got
        assert all(e["path"] and e["message"] for e in r.json()["errors"])

    def item(d: dict[str, Any]) -> dict[str, Any]:
        first: dict[str, Any] = d["timeline"]["tracks"][0]["items"][0]
        return first

    await expect_422(lambda d: d.update(schema_version="9.9"), "schema")
    await expect_422(lambda d: item(d).update(speed=99), "schema")
    await expect_422(lambda d: item(d).update(source_id="nope"), "unknown_source")
    await expect_422(lambda d: d["timeline"].update(duration_ms=1000), "exceeds_duration")
    await expect_422(
        lambda d: item(d).update(fade_in_ms=40000, source_in_ms=5000),
        "fade_too_long",
    )
    await expect_422(lambda d: d["canvas"].update(background=None), "schema")
    # referencias reales: la fuente debe ser un archivo de ESTA organización
    await expect_422(lambda d: d["sources"][0].update(asset_id=str(uuid.uuid4())), "unknown_asset")
    await expect_422(
        lambda d: item(d).update(source_in_ms=50_000, source_out_ms=65_000), "source_beyond_asset"
    )
    d = copy.deepcopy(doc)
    d["sources"][0]["kind"] = "audio"
    d["timeline"]["tracks"][0]["kind"] = "audio"
    r = await _save(ana, org, clip["id"], d, etag)
    assert r.status_code == 422 and "asset_kind_mismatch" in {e["code"] for e in r.json()["errors"]}
    # nada de lo rechazado cambió el documento ni la revisión
    after, after_etag = await _doc(ana, org, clip["id"])
    assert after == doc and after_etag == etag


async def test_document_references_must_be_ready_assets_of_the_organization(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    video = await _video(api, ana, org)
    clip = await _clip(ana, org, video)
    doc, etag = await _doc(ana, org, clip["id"])
    pending = str(await make_asset(api, org, ana.user_id, status="uploading"))
    d = copy.deepcopy(doc)
    d["sources"].append({"id": "s2", "asset_id": pending, "kind": "video"})
    r = await _save(ana, org, clip["id"], d, etag)
    assert r.status_code == 422 and {e["code"] for e in r.json()["errors"]} == {"asset_not_ready"}

    # un asset de OTRA organización no se puede referenciar aunque se conozca su id
    bob, org_b = await _setup(api, users, "bob@example.com")
    foreign = await _video(api, bob, org_b)
    d2 = copy.deepcopy(doc)
    d2["sources"][0]["asset_id"] = foreign
    r2 = await _save(ana, org, clip["id"], d2, etag)
    assert r2.status_code == 422 and {e["code"] for e in r2.json()["errors"]} == {"unknown_asset"}

    # un asset borrado tampoco
    spare = await _video(api, ana, org, title="Otro")
    d3 = copy.deepcopy(doc)
    d3["sources"][0]["asset_id"] = spare
    assert (await _save(ana, org, clip["id"], d3, etag)).status_code == 200
    # `spare` ya es fuente del documento: no se puede borrar aunque no sea el video de origen
    blocked = await ana.call("DELETE", f"/assets/{spare}", org=org)
    assert blocked.status_code == 409 and blocked.json()["code"] == "asset_in_use"


async def test_oversized_and_malformed_bodies(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    doc, etag = await _doc(ana, org, clip["id"])
    huge = copy.deepcopy(doc)
    huge["padding"] = "x" * 2_100_000
    r = await _save(ana, org, clip["id"], huge, etag)
    assert r.status_code == 413 and r.json()["code"] == "document_too_large"
    bodies: list[dict[str, Any]] = [
        {},
        {"document": "texto"},
        {"document": [], "x": 1},
        {"doc": {}},
    ]
    for body in bodies:
        r = await ana.call(
            "PATCH",
            f"/clips/{clip['id']}/edit-project",
            org=org,
            json=body,
            headers={"If-Match": etag},
        )
        assert r.status_code == 422, body


# --------------------------------------------------------------------------- versiones


async def test_versions_are_immutable_snapshots(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    doc, etag = await _doc(ana, org, clip["id"])

    v1 = await ana.call(
        "POST", f"/clips/{clip['id']}/versions", org=org, json={"label": "  Primer corte "}
    )
    assert v1.status_code == 201 and (v1.json()["version"], v1.json()["label"]) == (
        1,
        "Primer corte",
    )
    assert (await ana.call("GET", f"/clips/{clip['id']}", org=org)).json()[
        "current_version_id"
    ] == v1.json()["id"]

    changed = copy.deepcopy(doc)
    changed["timeline"]["tracks"][0]["items"][0]["volume"] = 0.3
    assert (await _save(ana, org, clip["id"], changed, etag)).status_code == 200
    v2 = await ana.call("POST", f"/clips/{clip['id']}/versions", org=org, json={})
    assert (v2.json()["version"], v2.json()["label"]) == (2, None)

    # la versión 1 conserva el documento de ese momento, aunque el clip siguió cambiando
    detail1 = (await ana.call("GET", f"/clips/{clip['id']}/versions/1", org=org)).json()
    detail2 = (await ana.call("GET", f"/clips/{clip['id']}/versions/2", org=org)).json()
    assert detail1["document"] == doc and detail2["document"] == changed
    assert (await ana.call("GET", f"/clips/{clip['id']}/versions/9", org=org)).status_code == 404
    assert (await ana.call("GET", f"/clips/{clip['id']}/versions/0", org=org)).status_code == 422
    assert (
        await ana.call("POST", f"/clips/{clip['id']}/versions", org=org, json={"label": "x" * 201})
    ).status_code == 422

    listing = (await ana.call("GET", f"/clips/{clip['id']}/versions?limit=1", org=org)).json()
    assert [v["version"] for v in listing["items"]] == [2] and listing["next_before"] == 2
    older = (await ana.call("GET", f"/clips/{clip['id']}/versions?before=2", org=org)).json()
    assert [v["version"] for v in older["items"]] == [1] and older["next_before"] is None
    assert "document" not in listing["items"][0]


async def test_database_rejects_changing_or_deleting_versions(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    version = (await ana.call("POST", f"/clips/{clip['id']}/versions", org=org, json={})).json()
    vid = uuid.UUID(version["id"])
    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await sql(api, "update clip_versions set label = 'hackeado' where id = $1", vid)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await sql(api, "update clip_versions set document = '{}'::jsonb where id = $1", vid)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await sql(api, "delete from clip_versions where id = $1", vid)
    assert (await sql(api, "select label from clip_versions where id = $1", vid))[0][
        "label"
    ] is None

    # solo el rol de sistema, habilitándolo en su transacción (retención), puede borrar
    dsn = make_url(api.db_urls["owner"]).set(drivername="postgresql")
    conn = await asyncpg.connect(dsn.render_as_string(hide_password=False))
    try:
        async with conn.transaction():
            await conn.execute("set local klick.allow_version_delete = 'on'")
            await conn.execute("delete from clip_versions where id = $1", vid)
        assert await conn.fetchval("select count(*) from clip_versions where id = $1", vid) == 0
    finally:
        await conn.close()


async def test_revision_can_never_go_backwards(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    doc, etag = await _doc(ana, org, clip["id"])
    assert (await _save(ana, org, clip["id"], doc, etag)).status_code == 200
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(
            api, "update edit_projects set revision = 1 where clip_id = $1", uuid.UUID(clip["id"])
        )


async def test_concurrent_version_creation_is_serialized(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    results = await asyncio.gather(
        *(ana.call("POST", f"/clips/{clip['id']}/versions", org=org, json={}) for _ in range(5))
    )
    assert all(r.status_code == 201 for r in results)
    assert sorted(r.json()["version"] for r in results) == [
        1,
        2,
        3,
        4,
        5,
    ]  # sin huecos ni repetidos


async def test_reuse_if_unchanged_avoids_duplicate_snapshots(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org))
    svc = api.app.state.clip_service
    p = principal(org, ana.user_id)
    first = await svc.create_version(p, uuid.UUID(clip["id"]), reuse_if_unchanged=True)
    again = await svc.create_version(p, uuid.UUID(clip["id"]), reuse_if_unchanged=True)
    assert again.id == first.id  # mismo documento: no se duplica
    labeled = await svc.create_version(
        p, uuid.UUID(clip["id"]), label="manual", reuse_if_unchanged=True
    )
    assert labeled.id != first.id and labeled.version == 2


# ------------------------------------------------------ permisos, aislamiento y trazabilidad


async def test_roles_and_cross_organization_isolation(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    viewer = await users.make("viewer@example.com")
    await add_member(api, ana, org, viewer, "viewer")
    editor = await users.make("editor@example.com")
    await add_member(api, ana, org, editor, "editor")
    asset = await _video(api, ana, org)
    clip = await _clip(ana, org, asset)
    doc, etag = await _doc(ana, org, clip["id"])

    for path in (
        "/clips",
        f"/clips/{clip['id']}",
        f"/clips/{clip['id']}/edit-project",
        f"/clips/{clip['id']}/versions",
    ):
        assert (await viewer.call("GET", path, org=org)).status_code == 200, path
    for method, path, body in [
        ("POST", "/clips", {"source_asset_id": asset, "start_ms": 0, "end_ms": 5000}),
        ("PATCH", f"/clips/{clip['id']}", {"title": "x"}),
        ("DELETE", f"/clips/{clip['id']}", None),
        ("PATCH", f"/clips/{clip['id']}/edit-project", {"document": doc}),
        ("POST", f"/clips/{clip['id']}/versions", {}),
    ]:
        r = await viewer.call(method, path, org=org, json=body, headers={"If-Match": etag})
        assert r.status_code == 403 and r.json()["errors"] == [{"permission": "clips:write"}], (
            method,
            path,
        )
    assert (
        await editor.call(
            "POST",
            "/clips",
            org=org,
            json={"source_asset_id": asset, "start_ms": 0, "end_ms": 5000},
        )
    ).status_code == 201

    bob, org_b = await _setup(api, users, "bob@example.com")
    for method, path, body in [
        ("GET", f"/clips/{clip['id']}", None),
        ("PATCH", f"/clips/{clip['id']}", {"title": "pwn"}),
        ("DELETE", f"/clips/{clip['id']}", None),
        ("GET", f"/clips/{clip['id']}/edit-project", None),
        ("PATCH", f"/clips/{clip['id']}/edit-project", {"document": {}}),
        ("GET", f"/clips/{clip['id']}/versions", None),
        ("POST", f"/clips/{clip['id']}/versions", {}),
        ("GET", f"/clips/{clip['id']}/versions/1", None),
    ]:
        r = await bob.call(method, path, org=org_b, json=body)  # sin If-Match a propósito
        assert r.status_code == 404, (method, path, r.status_code)
    assert (await bob.call("GET", "/clips", org=org_b)).json()["items"] == []
    stolen = await bob.call(
        "POST", "/clips", org=org_b, json={"source_asset_id": asset, "start_ms": 0, "end_ms": 5000}
    )
    assert stolen.status_code == 404  # no se puede clonar un video ajeno
    assert (await ana.call("GET", f"/clips/{clip['id']}", org=org)).json()["title"] == "Directo"


async def test_mutations_leave_audit_and_events_without_content(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    clip = await _clip(ana, org, await _video(api, ana, org), title="Secreto del torneo")
    await ana.call("PATCH", f"/clips/{clip['id']}", org=org, json={"title": "Otro secreto"})
    await ana.call(
        "POST", f"/clips/{clip['id']}/versions", org=org, json={"label": "versión privada"}
    )
    await ana.call("DELETE", f"/clips/{clip['id']}", org=org)
    actions = {
        r["action"]
        for r in await sql(api, "select action from audit_events where org_id = $1", uuid.UUID(org))
    }
    assert {"clip.created", "clip.updated", "clip.version_created", "clip.deleted"} <= actions
    payloads = " ".join(
        r["p"]
        for r in await sql(
            api,
            "select payload::text as p from outbox_events where org_id = $1 and type like 'clip.%'",
            uuid.UUID(org),
        )
    )
    assert (
        clip["id"] in payloads and "secreto" not in payloads.lower() and "privada" not in payloads
    )


async def test_composite_foreign_keys_block_cross_organization_references(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    bob, org_b = await _setup(api, users, "bob@example.com")
    foreign_asset = uuid.UUID(await _video(api, bob, org_b))
    foreign_project = uuid.UUID(
        (await bob.call("POST", "/projects", org=org_b, json={"name": "Ajeno"})).json()["id"]
    )
    own_asset = uuid.UUID(await _video(api, ana, org))
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await sql(
            api,
            "insert into clips (id, org_id, source_asset_id, title) values ($1, $2, $3, 't')",
            uuid.uuid4(),
            uuid.UUID(org),
            foreign_asset,
        )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await sql(
            api,
            "insert into clips (id, org_id, source_asset_id, title, project_id) values ($1, $2, $3, 't', $4)",
            uuid.uuid4(),
            uuid.UUID(org),
            own_asset,
            foreign_project,
        )
    assert media(api) is not None
