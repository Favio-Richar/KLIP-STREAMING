import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from klick.core.errors import ApiError
from media_helpers import make_asset, make_object, media, principal, sql

pytestmark = pytest.mark.db


async def _setup(
    api: ApiHarness, users: Users, email: str = "ana@example.com"
) -> tuple[Actor, str]:
    ana = await users.make(email)
    org = await create_org(ana, f"Org de {email}")
    return ana, org["id"]


# ------------------------------------------------------------------------------ proyectos


async def test_project_crud_and_name_rules(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    r = await ana.call(
        "POST", "/projects", org=org, json={"name": "  Canal Gaming ", "description": "Lives"}
    )
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["name"] == "Canal Gaming"  # se recorta

    dup = await ana.call("POST", "/projects", org=org, json={"name": "canal gaming"})
    assert dup.status_code == 409 and dup.json()["code"] == "project_name_taken"
    empty = await ana.call("POST", "/projects", org=org, json={"name": "   "})
    assert empty.status_code == 422
    extra = await ana.call("POST", "/projects", org=org, json={"name": "x", "org_id": org})
    assert extra.status_code == 422  # extra="forbid"

    patch = await ana.call(
        "PATCH", f"/projects/{project['id']}", org=org, json={"description": None, "name": "Gaming"}
    )
    assert patch.status_code == 200
    assert patch.json()["description"] is None and patch.json()["name"] == "Gaming"
    keep = await ana.call(
        "PATCH", f"/projects/{project['id']}", org=org, json={"settings": {"a": 1}}
    )
    assert keep.json()["name"] == "Gaming" and keep.json()["settings"] == {"a": 1}

    assert (await ana.call("DELETE", f"/projects/{project['id']}", org=org)).status_code == 204
    assert (await ana.call("GET", f"/projects/{project['id']}", org=org)).status_code == 404
    # el nombre vuelve a estar disponible tras el borrado lógico
    again = await ana.call("POST", "/projects", org=org, json={"name": "Gaming"})
    assert again.status_code == 201


async def test_project_listing_pagination_and_archive(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    ids = []
    for i in range(5):
        r = await ana.call("POST", "/projects", org=org, json={"name": f"P{i}"})
        ids.append(r.json()["id"])
    first = (await ana.call("GET", "/projects?limit=2", org=org)).json()
    assert [p["id"] for p in first["items"]] == ids[::-1][:2]  # más nuevos primero
    second = (
        await ana.call("GET", f"/projects?limit=2&cursor={first['next_cursor']}", org=org)
    ).json()
    assert [p["id"] for p in second["items"]] == ids[::-1][2:4]
    last = (
        await ana.call("GET", f"/projects?limit=2&cursor={second['next_cursor']}", org=org)
    ).json()
    assert len(last["items"]) == 1 and last["next_cursor"] is None

    await ana.call("PATCH", f"/projects/{ids[0]}", org=org, json={"archived": True})
    visible = (await ana.call("GET", "/projects?limit=50", org=org)).json()["items"]
    assert ids[0] not in [p["id"] for p in visible]
    with_archived = (await ana.call("GET", "/projects?include_archived=true", org=org)).json()
    assert ids[0] in [p["id"] for p in with_archived["items"]]


# -------------------------------------------------------------------------------- carpetas


async def test_folders_tree_rules(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    proj = (await ana.call("POST", "/projects", org=org, json={"name": "P"})).json()["id"]
    other = (await ana.call("POST", "/projects", org=org, json={"name": "Q"})).json()["id"]

    root = (
        await ana.call("POST", "/folders", org=org, json={"name": "Raíz", "project_id": proj})
    ).json()
    child = (
        await ana.call(
            "POST",
            "/folders",
            org=org,
            json={"name": "Hijo", "project_id": proj, "parent_id": root["id"]},
        )
    ).json()
    assert child["parent_id"] == root["id"]

    dup = await ana.call("POST", "/folders", org=org, json={"name": "raíz", "project_id": proj})
    assert dup.status_code == 409 and dup.json()["code"] == "folder_name_taken"
    # el mismo nombre en otro proyecto sí se permite
    ok = await ana.call("POST", "/folders", org=org, json={"name": "Raíz", "project_id": other})
    assert ok.status_code == 201
    # un hijo no puede vivir en otro proyecto que su padre
    mismatch = await ana.call(
        "POST",
        "/folders",
        org=org,
        json={"name": "X", "project_id": other, "parent_id": root["id"]},
    )
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "folder_project_mismatch"
    # proyecto inexistente
    ghost = await ana.call(
        "POST", "/folders", org=org, json={"name": "G", "project_id": str(uuid.uuid4())}
    )
    assert ghost.status_code == 404

    # ciclos: mover la raíz dentro de su hijo
    cyc = await ana.call(
        "PATCH", f"/folders/{root['id']}", org=org, json={"parent_id": child["id"]}
    )
    assert cyc.status_code == 422 and cyc.json()["code"] == "folder_cycle"
    self_parent = await ana.call(
        "PATCH", f"/folders/{root['id']}", org=org, json={"parent_id": root["id"]}
    )
    assert self_parent.status_code == 422

    # listados: raíz por defecto; recursive trae todo el proyecto
    top = (await ana.call("GET", f"/folders?project_id={proj}", org=org)).json()
    assert [f["name"] for f in top] == ["Raíz"]
    nested = (
        await ana.call("GET", f"/folders?project_id={proj}&parent_id={root['id']}", org=org)
    ).json()
    assert [f["name"] for f in nested] == ["Hijo"]
    everything = (
        await ana.call("GET", f"/folders?project_id={proj}&recursive=true", org=org)
    ).json()
    assert {f["name"] for f in everything} == {"Raíz", "Hijo"}

    # mover el hijo a la raíz (null explícito) y renombrar
    moved = await ana.call(
        "PATCH", f"/folders/{child['id']}", org=org, json={"parent_id": None, "name": "Suelto"}
    )
    assert moved.status_code == 200 and moved.json()["parent_id"] is None

    # borrar la raíz arrastra subcarpetas pero no los assets (quedan sin carpeta)
    sub = (
        await ana.call(
            "POST",
            "/folders",
            org=org,
            json={"name": "Sub", "project_id": proj, "parent_id": root["id"]},
        )
    ).json()
    asset = await make_asset(
        api, org, ana.user_id, project_id=uuid.UUID(proj), folder_id=uuid.UUID(sub["id"])
    )
    assert (await ana.call("DELETE", f"/folders/{root['id']}", org=org)).status_code == 204
    assert (await ana.call("GET", f"/assets/{asset}", org=org)).json()["folder_id"] is None
    left = (await ana.call("GET", f"/folders?project_id={proj}&recursive=true", org=org)).json()
    assert {f["name"] for f in left} == {"Suelto"}


async def test_folder_depth_limit(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    parent: str | None = None
    for level in range(10):
        r = await ana.call(
            "POST", "/folders", org=org, json={"name": f"n{level}", "parent_id": parent}
        )
        assert r.status_code == 201, (level, r.text)
        parent = r.json()["id"]
    too_deep = await ana.call(
        "POST", "/folders", org=org, json={"name": "n10", "parent_id": parent}
    )
    assert too_deep.status_code == 422 and too_deep.json()["code"] == "folder_too_deep"


# -------------------------------------------------------------------------------- etiquetas


async def test_tags_and_filtering(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    t1 = (await ana.call("POST", "/tags", org=org, json={"name": "Torneo"})).json()
    t2 = (await ana.call("POST", "/tags", org=org, json={"name": "Highlights"})).json()
    dup = await ana.call("POST", "/tags", org=org, json={"name": "torneo"})
    assert dup.status_code == 409 and dup.json()["code"] == "tag_name_taken"
    assert [t["name"] for t in (await ana.call("GET", "/tags?q=high", org=org)).json()] == [
        "Highlights"
    ]

    a1 = await make_asset(api, org, ana.user_id, title="Final")
    a2 = await make_asset(api, org, ana.user_id, title="Semifinal")
    await ana.call("PATCH", f"/assets/{a1}", org=org, json={"tag_ids": [t1["id"], t2["id"]]})
    await ana.call("PATCH", f"/assets/{a2}", org=org, json={"tag_ids": [t1["id"]]})

    both = (await ana.call("GET", f"/assets?tag_id={t1['id']}&tag_id={t2['id']}", org=org)).json()
    assert [a["id"] for a in both["items"]] == [str(a1)]  # deben tener todas las etiquetas
    one = (await ana.call("GET", f"/assets?tag_id={t1['id']}", org=org)).json()
    assert {a["id"] for a in one["items"]} == {str(a1), str(a2)}
    assert {t["name"] for t in one["items"][0]["tags"]} <= {"Torneo", "Highlights"}

    unknown = await ana.call(
        "PATCH", f"/assets/{a1}", org=org, json={"tag_ids": [str(uuid.uuid4())]}
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_tag"
    # la etiqueta fallida no dejó el asset a medias
    assert len((await ana.call("GET", f"/assets/{a1}", org=org)).json()["tags"]) == 2

    assert (await ana.call("DELETE", f"/tags/{t1['id']}", org=org)).status_code == 204
    assert [
        t["name"] for t in (await ana.call("GET", f"/assets/{a1}", org=org)).json()["tags"]
    ] == ["Highlights"]
    cleared = await ana.call("PATCH", f"/assets/{a1}", org=org, json={"tag_ids": []})
    assert cleared.json()["tags"] == []


# ---------------------------------------------------------------------------------- assets


async def test_asset_listing_filters_search_and_pagination(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    proj = (await ana.call("POST", "/projects", org=org, json={"name": "P"})).json()["id"]
    v1 = await make_asset(api, org, ana.user_id, title="Directo de ajedrez", status="ready")
    v2 = await make_asset(
        api, org, ana.user_id, title="Torneo de ajedrez", project_id=uuid.UUID(proj)
    )
    a1 = await make_asset(
        api, org, ana.user_id, title="Música de fondo", kind="audio", status="failed"
    )
    v3 = await make_asset(api, org, ana.user_id, title="Receta de pasta_100%", status="processing")

    def ids(resp: Any) -> list[str]:
        return [a["id"] for a in resp.json()["items"]]

    assert ids(await ana.call("GET", "/assets", org=org)) == [str(x) for x in (v3, a1, v2, v1)]
    assert set(ids(await ana.call("GET", "/assets?kind=audio", org=org))) == {str(a1)}
    assert set(ids(await ana.call("GET", "/assets?status=ready", org=org))) == {str(v1)}
    assert set(ids(await ana.call("GET", f"/assets?project_id={proj}", org=org))) == {str(v2)}
    # texto completo en español (singular/plural) y subcadena
    assert set(ids(await ana.call("GET", "/assets?q=ajedrez", org=org))) == {str(v1), str(v2)}
    assert set(ids(await ana.call("GET", "/assets?q=torn", org=org))) == {str(v2)}
    # los comodines de LIKE se tratan como texto literal
    assert set(ids(await ana.call("GET", "/assets?q=100%25", org=org))) == {str(v3)}
    assert ids(await ana.call("GET", "/assets?q=%25", org=org)) == [str(v3)]
    assert (await ana.call("GET", "/assets?kind=otro", org=org)).status_code == 422

    page1 = (await ana.call("GET", "/assets?limit=3", org=org)).json()
    page2 = (
        await ana.call("GET", f"/assets?limit=3&cursor={page1['next_cursor']}", org=org)
    ).json()
    assert len(page1["items"]) == 3 and len(page2["items"]) == 1 and page2["next_cursor"] is None

    detail = (await ana.call("GET", f"/assets/{v1}", org=org)).json()
    assert detail["duration_ms"] == 60_000 and detail["fps"] == 30.0 and detail["status"] == "ready"
    assert "storage_object_id" not in detail and "probe" not in detail  # no se exponen internos


async def test_asset_update_placement_and_soft_delete(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    p1 = (await ana.call("POST", "/projects", org=org, json={"name": "P1"})).json()["id"]
    p2 = (await ana.call("POST", "/projects", org=org, json={"name": "P2"})).json()["id"]
    f1 = (await ana.call("POST", "/folders", org=org, json={"name": "F", "project_id": p1})).json()[
        "id"
    ]
    asset = await make_asset(api, org, ana.user_id)

    bad = await ana.call("PATCH", f"/assets/{asset}", org=org, json={"folder_id": f1})
    assert bad.status_code == 422 and bad.json()["code"] == "folder_project_mismatch"
    ok = await ana.call(
        "PATCH",
        f"/assets/{asset}",
        org=org,
        json={"project_id": p1, "folder_id": f1, "title": "Nuevo"},
    )
    assert ok.status_code == 200
    assert (ok.json()["project_id"], ok.json()["folder_id"], ok.json()["title"]) == (
        p1,
        f1,
        "Nuevo",
    )
    # cambiar de proyecto saca al asset de la carpeta anterior
    moved = await ana.call("PATCH", f"/assets/{asset}", org=org, json={"project_id": p2})
    assert moved.json()["project_id"] == p2 and moved.json()["folder_id"] is None
    cleared = await ana.call("PATCH", f"/assets/{asset}", org=org, json={"project_id": None})
    assert cleared.json()["project_id"] is None
    # PATCH sin cambios no toca nada
    same = await ana.call("PATCH", f"/assets/{asset}", org=org, json={})
    assert same.status_code == 200 and same.json()["title"] == "Nuevo"

    assert (await ana.call("DELETE", f"/assets/{asset}", org=org)).status_code == 204
    assert (await ana.call("GET", f"/assets/{asset}", org=org)).status_code == 404
    assert (
        await ana.call("PATCH", f"/assets/{asset}", org=org, json={"title": "x"})
    ).status_code == 404
    assert (await ana.call("DELETE", f"/assets/{asset}", org=org)).status_code == 404
    assert str(asset) not in [
        a["id"] for a in (await ana.call("GET", "/assets", org=org)).json()["items"]
    ]
    row = (
        await sql(api, "select deleted_at is not null as d from media_assets where id = $1", asset)
    )[0]
    assert row["d"]  # sigue en la base hasta que la retención lo elimine


async def test_download_url_and_derivatives(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    pending = await make_asset(api, org, ana.user_id, status="uploading")
    blocked = await ana.call("GET", f"/assets/{pending}/download-url", org=org)
    assert blocked.status_code == 409 and blocked.json()["code"] == "asset_not_ready"

    ready = await make_asset(api, org, ana.user_id, status="ready")
    r = await ana.call("GET", f"/assets/{ready}/download-url", org=org)
    assert r.status_code == 200
    assert (
        r.json()["url"].startswith("http")
        and r.json()["expires_in"] == api.settings.storage_url_ttl_s
    )

    assert (await ana.call("GET", f"/assets/{ready}/derivatives", org=org)).json() == []
    thumb = await make_object(api, org, ana.user_id)
    await media(api).upsert_derivative(uuid.UUID(org), ready, "thumbnail", thumb, {"w": 320})
    listed = (await ana.call("GET", f"/assets/{ready}/derivatives", org=org)).json()
    assert [(d["kind"], d["meta"]) for d in listed] == [("thumbnail", {"w": 320})]
    assert "storage_object_id" not in listed[0]
    with pytest.raises(ApiError) as exc:
        await media(api).upsert_derivative(uuid.UUID(org), ready, "bogus", thumb)
    assert exc.value.code == "invalid_derivative_kind"


async def test_thumbnail_urls_in_listings_and_derivative_download_urls(
    api: ApiHarness, users: Users
) -> None:
    import httpx  # noqa: PLC0415

    ana, org = await _setup(api, users)
    org_id = uuid.UUID(org)
    with_thumb = await make_asset(api, org, ana.user_id, title="Con miniatura", status="ready")
    without = await make_asset(api, org, ana.user_id, title="Sin miniatura", status="ready")
    thumb_obj = await make_object(api, org, ana.user_id, data=b"jpeg-bytes")
    proxy_obj = await make_object(api, org, ana.user_id, data=b"proxy-bytes")
    await media(api).upsert_derivative(org_id, with_thumb, "thumbnail", thumb_obj)
    await media(api).upsert_derivative(org_id, with_thumb, "proxy_720p", proxy_obj)

    listing = {a["id"]: a for a in (await ana.call("GET", "/assets", org=org)).json()["items"]}
    assert listing[str(with_thumb)]["thumbnail_url"].startswith("http")
    assert listing[str(without)]["thumbnail_url"] is None
    detail = (await ana.call("GET", f"/assets/{with_thumb}", org=org)).json()
    assert detail["thumbnail_url"] is not None

    r = await ana.call("GET", f"/assets/{with_thumb}/derivatives/proxy_720p/download-url", org=org)
    assert r.status_code == 200 and r.json()["expires_in"] == api.settings.storage_url_ttl_s
    async with httpx.AsyncClient() as http:
        assert (
            await http.get(r.json()["url"])
        ).content == b"proxy-bytes"  # la URL funciona de verdad

    missing = await ana.call(
        "GET", f"/assets/{with_thumb}/derivatives/sprite/download-url", org=org
    )
    assert missing.status_code == 404  # ese derivado aún no existe
    bad = await ana.call("GET", f"/assets/{with_thumb}/derivatives/otro/download-url", org=org)
    assert bad.status_code == 422
    nothing = await ana.call(
        "GET", f"/assets/{uuid.uuid4()}/derivatives/thumbnail/download-url", org=org
    )
    assert nothing.status_code == 404

    # otra organización no obtiene ni miniaturas ni URLs de derivados ajenos
    bob, org_b = await _setup(api, users, "bob@example.com")
    cross = await bob.call(
        "GET", f"/assets/{with_thumb}/derivatives/proxy_720p/download-url", org=org_b
    )
    assert cross.status_code == 404
    assert (await bob.call("GET", "/assets", org=org_b)).json()["items"] == []
    viewer = await users.make("viewer2@example.com")
    await add_member(api, ana, org, viewer, "viewer")
    ok = await viewer.call(
        "GET", f"/assets/{with_thumb}/derivatives/thumbnail/download-url", org=org
    )
    assert ok.status_code == 200  # media:read basta para ver


# ---------------------------------------------------------------------------------- permisos


async def test_viewer_reads_but_cannot_write(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    viewer = await users.make("viewer@example.com")
    await add_member(api, ana, org, viewer, "viewer")
    editor = await users.make("editor@example.com")
    await add_member(api, ana, org, editor, "editor")
    asset = await make_asset(api, org, ana.user_id)
    proj = (await ana.call("POST", "/projects", org=org, json={"name": "P"})).json()["id"]

    for path in (
        "/projects",
        "/folders",
        "/tags",
        "/assets",
        f"/assets/{asset}",
        f"/projects/{proj}",
    ):
        assert (await viewer.call("GET", path, org=org)).status_code == 200, path
    writes = [
        ("POST", "/projects", {"name": "X"}),
        ("PATCH", f"/projects/{proj}", {"name": "Y"}),
        ("DELETE", f"/projects/{proj}", None),
        ("POST", "/folders", {"name": "F"}),
        ("POST", "/tags", {"name": "T"}),
        ("PATCH", f"/assets/{asset}", {"title": "Z"}),
        ("DELETE", f"/assets/{asset}", None),
    ]
    for method, path, body in writes:
        r = await viewer.call(method, path, org=org, json=body)
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json()["errors"] == [{"permission": "media:write"}]
    assert (
        await editor.call("POST", "/projects", org=org, json={"name": "Editor"})
    ).status_code == 201


# ---------------------------------------------------------------- estados e integridad (BD)


async def test_asset_state_machine(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    org_id = uuid.UUID(org)
    asset = await make_asset(api, org, ana.user_id)  # uploading, objeto sin subir
    svc = media(api)

    with pytest.raises(ApiError) as exc:  # uploading no salta directo a ready
        await svc.transition_asset(org_id, asset, "ready", duration_ms=1000)
    assert exc.value.code == "invalid_asset_transition"

    await svc.transition_asset(org_id, asset, "processing")
    with pytest.raises(ApiError) as exc:  # sin duración
        await svc.transition_asset(org_id, asset, "ready")
    assert exc.value.code == "missing_duration"
    with pytest.raises(ApiError) as exc:  # campo técnico desconocido
        await svc.transition_asset(org_id, asset, "ready", duration_ms=1, storage_object_id="x")
    assert exc.value.code == "invalid_asset_metadata"
    # el objeto del almacén sigue en 'uploading': la BD rechaza dejar el asset listo
    with pytest.raises(ApiError) as exc:
        await svc.transition_asset(org_id, asset, "ready", duration_ms=1000)
    assert exc.value.code == "asset_object_not_ready"
    assert (await svc.get_asset(org_id, asset)).asset.status == "processing"  # rollback completo

    ready = await make_asset(api, org, ana.user_id, status="ready")
    view = await svc.get_asset(org_id, ready)
    assert view.asset.status == "ready" and view.asset.error is None
    await svc.transition_asset(org_id, ready, "processing")  # reanalizar
    await svc.transition_asset(org_id, ready, "failed", error={"code": "corrupt", "detail": "x"})
    failed = (await svc.get_asset(org_id, ready)).asset
    assert failed.error == {"code": "corrupt", "detail": "x"}
    await svc.transition_asset(org_id, ready, "processing")
    assert (await svc.get_asset(org_id, ready)).asset.error is None  # reanalizar limpia el error

    image = await make_asset(
        api, org, ana.user_id, kind="image", title="Portada", status="processing"
    )
    await svc.transition_asset(org_id, image, "ready", width=800, height=600)  # imagen sin duración
    assert (await svc.get_asset(org_id, image)).asset.status == "ready"


async def test_storage_ref_counting_is_maintained_by_triggers(
    api: ApiHarness, users: Users
) -> None:
    ana, org = await _setup(api, users)
    org_id = uuid.UUID(org)

    async def state(obj: uuid.UUID) -> tuple[int, str]:
        row = (await sql(api, "select ref_count, status from storage_objects where id = $1", obj))[
            0
        ]
        return row["ref_count"], row["status"]

    obj = await make_object(api, org, ana.user_id)
    assert await state(obj) == (0, "ready")
    asset = await media(api).create_asset(
        principal(org, ana.user_id),
        kind="video",
        title="V",
        storage_object_id=obj,
        rights_confirmed=True,
    )
    assert await state(obj) == (1, "ready")

    thumb = await make_object(api, org, ana.user_id)
    other = await make_object(api, org, ana.user_id)
    await media(api).upsert_derivative(org_id, asset.id, "thumbnail", thumb)
    assert await state(thumb) == (1, "ready")
    await media(api).upsert_derivative(org_id, asset.id, "thumbnail", other)  # reemplazo
    assert await state(thumb) == (0, "orphan")  # el anterior queda huérfano
    assert await state(other) == (1, "ready")
    await media(api).upsert_derivative(org_id, asset.id, "thumbnail", thumb)  # revive al huérfano
    assert await state(thumb) == (1, "ready")
    assert (await sql(api, "select orphaned_at from storage_objects where id = $1", thumb))[0][
        0
    ] is None

    # el borrado definitivo (retención) libera las referencias en cascada
    await sql(api, "delete from media_assets where id = $1", asset.id)
    assert await state(obj) == (0, "orphan")
    assert await state(thumb) == (0, "orphan")


async def test_database_constraints_hold_without_the_service_layer(
    api: ApiHarness, users: Users
) -> None:
    """Defensa en profundidad: aunque se salte el servicio, la BD impide estados inválidos."""
    ana, org = await _setup(api, users)
    bob, org_b = await _setup(api, users, "bob@example.com")
    obj = await make_object(api, org, ana.user_id)

    async def insert_asset(**over: Any) -> None:
        cols: dict[str, Any] = {
            "id": uuid.uuid4(),
            "org_id": uuid.UUID(org),
            "kind": "video",
            "title": "t",
            "status": "uploading",
            "storage_object_id": obj,
            "rights_confirmed_at": datetime.now(UTC),
            **over,
        }
        names = ", ".join(cols)
        marks = ", ".join(f"${i + 1}" for i in range(len(cols)))
        await sql(api, f"insert into media_assets ({names}) values ({marks})", *cols.values())

    with pytest.raises(asyncpg.CheckViolationError):  # subido sin confirmar derechos
        await insert_asset(rights_confirmed_at=None)
    with pytest.raises(asyncpg.CheckViolationError):  # listo sin duración
        await insert_asset(status="ready")
    with pytest.raises(asyncpg.CheckViolationError):
        await insert_asset(kind="document")
    with pytest.raises(asyncpg.CheckViolationError):
        await insert_asset(title="   ")
    with pytest.raises(asyncpg.CheckViolationError):
        await insert_asset(width=0)
    # objeto de otra organización: la FK compuesta lo impide
    foreign = await make_object(api, org_b, bob.user_id)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await insert_asset(storage_object_id=foreign)
    # proyecto de otra organización
    foreign_project = (
        await bob.call("POST", "/projects", org=org_b, json={"name": "Ajeno"})
    ).json()["id"]
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await insert_asset(project_id=uuid.UUID(foreign_project))
    # carpeta hija con proyecto distinto al del padre
    p1 = (await ana.call("POST", "/projects", org=org, json={"name": "A"})).json()["id"]
    parent = (
        await ana.call("POST", "/folders", org=org, json={"name": "P", "project_id": p1})
    ).json()["id"]
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(
            api,
            "insert into folders (id, org_id, parent_id, name) values ($1, $2, $3, 'x')",
            uuid.uuid4(),
            uuid.UUID(org),
            uuid.UUID(parent),
        )


async def test_assets_require_rights_confirmation(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    obj = await make_object(api, org, ana.user_id, ready=False)
    with pytest.raises(ApiError) as exc:
        await media(api).create_asset(
            principal(org, ana.user_id), kind="video", title="x", storage_object_id=obj
        )
    assert exc.value.status == 422 and exc.value.code == "rights_not_confirmed"
    asset = await media(api).create_asset(
        principal(org, ana.user_id),
        kind="video",
        title="x",
        storage_object_id=obj,
        rights_confirmed=True,
    )
    row = (
        await sql(
            api,
            "select rights_confirmed_at, rights_confirmed_by from media_assets where id = $1",
            asset.id,
        )
    )[0]
    assert row["rights_confirmed_at"] is not None
    assert str(row["rights_confirmed_by"]) == ana.user_id  # queda registrado quién y cuándo


# ----------------------------------------------------------------------------- subidas


async def test_upload_lifecycle(api: ApiHarness, users: Users) -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    ana, org = await _setup(api, users)
    org_id, svc = uuid.UUID(org), media(api)
    p = principal(org, ana.user_id)
    obj = await make_object(api, org, ana.user_id, ready=False)
    asset = await svc.create_asset(
        p, kind="video", title="Grande", storage_object_id=obj, rights_confirmed=True
    )
    up = await svc.create_upload(
        p,
        asset_id=asset.id,
        storage_object_id=obj,
        s3_upload_id="mp-1",
        part_size=8 * 1024 * 1024,
        size_bytes=100,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert up.status == "open"
    done = await svc.transition_upload(org_id, up.id, "completing", parts=[{"n": 1, "etag": "e"}])
    assert done.parts == [{"n": 1, "etag": "e"}]
    await svc.transition_upload(org_id, up.id, "completed")
    with pytest.raises(ApiError) as exc:
        await svc.transition_upload(org_id, up.id, "aborted")
    assert exc.value.code == "invalid_upload_transition"

    stale = await svc.create_upload(
        p,
        asset_id=asset.id,
        storage_object_id=obj,
        s3_upload_id=None,
        part_size=None,
        size_bytes=None,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert await svc.expire_stale_uploads(org_id) == 1
    assert (await svc.get_upload(org_id, stale.id)).status == "expired"
    assert (await svc.get_upload(org_id, up.id)).status == "completed"  # no se toca


# ------------------------------------------------------------------ aislamiento y trazabilidad


async def test_other_organizations_cannot_see_or_use_media(api: ApiHarness, users: Users) -> None:
    ana, org_a = await _setup(api, users)
    bob, org_b = await _setup(api, users, "bob@example.com")
    proj = (await ana.call("POST", "/projects", org=org_a, json={"name": "Secreto"})).json()["id"]
    tag = (await ana.call("POST", "/tags", org=org_a, json={"name": "interno"})).json()["id"]
    folder = (await ana.call("POST", "/folders", org=org_a, json={"name": "F"})).json()["id"]
    asset = await make_asset(api, org_a, ana.user_id, status="ready")

    for method, path, body in [
        ("GET", f"/projects/{proj}", None),
        ("PATCH", f"/projects/{proj}", {"name": "pwn"}),
        ("DELETE", f"/projects/{proj}", None),
        ("PATCH", f"/folders/{folder}", {"name": "pwn"}),
        ("DELETE", f"/folders/{folder}", None),
        ("DELETE", f"/tags/{tag}", None),
        ("GET", f"/assets/{asset}", None),
        ("PATCH", f"/assets/{asset}", {"title": "pwn"}),
        ("DELETE", f"/assets/{asset}", None),
        ("GET", f"/assets/{asset}/derivatives", None),
        ("GET", f"/assets/{asset}/download-url", None),
    ]:
        r = await bob.call(method, path, org=org_b, json=body)
        assert r.status_code == 404, (method, path, r.status_code)
    for path in ("/projects", "/folders", "/tags", "/assets"):
        listing = (await bob.call("GET", path, org=org_b)).json()
        assert (listing["items"] if isinstance(listing, dict) else listing) == [], path
    # bob no puede colocar su asset en el proyecto/carpeta/etiqueta de ana
    mine = await make_asset(api, org_b, bob.user_id)
    bodies: list[dict[str, Any]] = [{"project_id": proj}, {"folder_id": folder}, {"tag_ids": [tag]}]
    for body in bodies:
        r = await bob.call("PATCH", f"/assets/{mine}", org=org_b, json=body)
        assert r.status_code in (404, 422), (body, r.status_code)
    # ni referenciar un objeto de almacenamiento ajeno al crear un asset
    foreign_obj = await make_object(api, org_a, ana.user_id)
    with pytest.raises(ApiError) as exc:
        await media(api).create_asset(
            principal(org_b, bob.user_id),
            kind="video",
            title="x",
            storage_object_id=foreign_obj,
            rights_confirmed=True,
        )
    assert exc.value.status == 404
    # el asset de ana sigue intacto
    intact = (await ana.call("GET", f"/assets/{asset}", org=org_a)).json()
    assert intact["title"] == "Directo de ajedrez" and intact["project_id"] is None


async def test_mutations_leave_audit_and_events(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    proj = (await ana.call("POST", "/projects", org=org, json={"name": "Auditado"})).json()["id"]
    asset = await make_asset(api, org, ana.user_id)
    await ana.call("PATCH", f"/assets/{asset}", org=org, json={"title": "Otro"})
    await ana.call("DELETE", f"/assets/{asset}", org=org)
    await ana.call("DELETE", f"/projects/{proj}", org=org)

    actions = {
        r["action"]
        for r in await sql(api, "select action from audit_events where org_id = $1", uuid.UUID(org))
    }
    assert {
        "project.created",
        "project.deleted",
        "asset.created",
        "asset.updated",
        "asset.deleted",
    } <= actions
    events = {
        r["type"]
        for r in await sql(api, "select type from outbox_events where org_id = $1", uuid.UUID(org))
    }
    assert {"asset.created", "asset.updated", "asset.deleted", "project.created"} <= events
    payload = (
        await sql(
            api,
            "select payload::text as p from outbox_events where org_id = $1 and type = 'asset.updated'",
            uuid.UUID(org),
        )
    )[0]["p"]
    assert str(asset) in payload and "Otro" not in payload  # el evento no filtra contenido
