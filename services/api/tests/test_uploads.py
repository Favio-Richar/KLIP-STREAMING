import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from klick.media.uploads import (
    choose_part_size,
    clean_filename,
    detect_type,
    reap_expired_uploads,
)
from media_helpers import sql

pytestmark = pytest.mark.db

MIB = 1024 * 1024
PART = 5 * MIB  # mínimo de S3; las pruebas fuerzan este tamaño de parte


@pytest.fixture(autouse=True)
def small_parts(api: ApiHarness) -> None:
    api.settings.upload_part_min_bytes = PART


async def _setup(
    api: ApiHarness, users: Users, email: str = "ana@example.com"
) -> tuple[Actor, str]:
    ana = await users.make(email)
    return ana, (await create_org(ana, f"Org {email}"))["id"]


def _body(size: int = 11 * MIB, **over: Any) -> dict[str, Any]:
    return {
        "filename": "Directo de ajedrez.mp4",
        "size_bytes": size,
        "content_type": "video/mp4",
        "rights_confirmed": True,
        **over,
    }


async def _start(ana: Actor, org: str, **over: Any) -> dict[str, Any]:
    r = await ana.call("POST", "/assets/uploads", org=org, json=_body(**over))
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


async def _put_parts(
    ana: Actor, org: str, upload_id: str, sizes: dict[int, int], fill: bytes = b"x"
) -> None:
    """Sube las partes indicadas {número: tamaño} directo al almacén con URLs firmadas."""
    r = await ana.call(
        "POST", f"/assets/uploads/{upload_id}/parts", org=org, json={"part_numbers": list(sizes)}
    )
    assert r.status_code == 200, r.text
    async with httpx.AsyncClient(timeout=60) as http:
        for number, size in sizes.items():
            put = await http.put(r.json()["urls"][str(number)], content=fill * size)
            assert put.status_code == 200, (number, put.status_code, put.text)


# ----------------------------------------------------------------------- funciones puras


def test_part_size_respects_minimum_and_part_limit() -> None:
    assert choose_part_size(1, 8 * MIB) == 8 * MIB
    assert choose_part_size(1, 1) == 5 * MIB  # nunca bajo el mínimo de S3
    big = 20 * 1024**3
    size = choose_part_size(big, 8 * MIB)
    assert size % MIB == 0 and -(-big // size) <= 10_000  # cabe en el máximo de partes


def test_filenames_are_sanitized() -> None:
    assert clean_filename("../../etc/passwd.mp4") == "passwd.mp4"
    assert clean_filename("C:\\Users\\x\\video.mov") == "video.mov"
    assert clean_filename('a<b>:"c|d?.mp4') == "abcd.mp4"
    assert clean_filename("bad\x00\x1b name.mp4") == "bad name.mp4"
    assert clean_filename("   ...  ") == ""
    assert len(clean_filename("a" * 500 + ".mp4")) == 200


def test_type_detection() -> None:
    assert detect_type("a.mp4", "video/mp4") == ("video/mp4", "video")
    assert detect_type("a.mkv", "") == ("video/x-matroska", "video")  # el navegador no informó tipo
    assert detect_type("a.mp3", "application/octet-stream") == ("audio/mpeg", "audio")
    assert detect_type("a.png", "image/png; charset=x") == ("image/png", "image")
    with pytest.raises(Exception) as exc:
        detect_type("virus.exe", "application/x-msdownload")
    assert getattr(exc.value, "status", None) == 415


# ----------------------------------------------------------------------------- flujo


async def test_full_upload_flow(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    started = await _start(ana, org, title="  Final  ")
    up, asset = started["upload"], started["asset"]
    assert started["deduplicated"] is False
    assert (up["part_size"], up["total_parts"], up["uploaded_parts"]) == (PART, 3, [])
    assert asset["status"] == "uploading" and asset["title"] == "Final" and asset["kind"] == "video"

    await _put_parts(ana, org, up["id"], {1: PART, 2: PART, 3: MIB})
    state = (await ana.call("GET", f"/assets/uploads/{up['id']}", org=org)).json()
    assert state["uploaded_parts"] == [1, 2, 3] and state["status"] == "open"

    done = await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    assert done.status_code == 200, done.text
    assert done.json()["id"] == asset["id"] and done.json()["status"] == "processing"

    # el objeto existe en el almacén con el tamaño real y quedó referenciado una vez
    row = (
        await sql(
            api,
            "select o.status, o.size_bytes, o.ref_count, a.rights_confirmed_at is not null as rights "
            "from media_assets a join storage_objects o on o.id = a.storage_object_id where a.id = $1",
            uuid.UUID(asset["id"]),
        )
    )[0]
    assert (row["status"], row["size_bytes"], row["ref_count"], row["rights"]) == (
        "ready",
        11 * MIB,
        1,
        True,
    )
    assert (await ana.call("GET", f"/assets/uploads/{up['id']}", org=org)).json()[
        "status"
    ] == "completed"
    # completar otra vez es idempotente
    again = await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    assert again.status_code == 200 and again.json()["id"] == asset["id"]
    # y ya no se piden más URLs ni se aborta
    assert (
        await ana.call(
            "POST", f"/assets/uploads/{up['id']}/parts", org=org, json={"part_numbers": [1]}
        )
    ).status_code == 409
    assert (await ana.call("POST", f"/assets/uploads/{up['id']}/abort", org=org)).status_code == 409

    actions = {
        r["action"]
        for r in await sql(api, "select action from audit_events where org_id = $1", uuid.UUID(org))
    }
    assert "upload.completed" in actions
    events = {
        r["type"]
        for r in await sql(api, "select type from outbox_events where org_id = $1", uuid.UUID(org))
    }
    assert "asset.uploaded" in events


async def test_resume_after_interruption(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    up = (await _start(ana, org))["upload"]
    await _put_parts(ana, org, up["id"], {1: PART, 3: MIB})  # la parte 2 nunca llegó

    state = (await ana.call("GET", f"/assets/uploads/{up['id']}", org=org)).json()
    assert state["uploaded_parts"] == [1, 3]  # el cliente sabe qué falta

    early = await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    assert early.status_code == 409 and early.json()["code"] == "upload_incomplete"
    assert early.json()["errors"] == [{"missing_parts": [2]}]
    # la subida sigue abierta y se puede continuar
    assert (await ana.call("GET", f"/assets/uploads/{up['id']}", org=org)).json()[
        "status"
    ] == "open"
    await _put_parts(ana, org, up["id"], {2: PART})
    assert (
        await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    ).status_code == 200


async def test_size_mismatch_is_rejected_and_cleaned_up(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    started = await _start(ana, org)  # declara 11 MiB
    up, asset = started["upload"], started["asset"]
    await _put_parts(ana, org, up["id"], {1: PART, 2: PART, 3: 2 * MIB})  # sube 12 MiB
    r = await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    assert r.status_code == 422 and r.json()["code"] == "upload_size_mismatch"
    assert (await ana.call("GET", f"/assets/{asset['id']}", org=org)).status_code == 404
    assert (await ana.call("GET", f"/assets/uploads/{up['id']}", org=org)).status_code == 404
    key = (await sql(api, "select key from storage_objects where org_id = $1", uuid.UUID(org)))[0][
        0
    ]
    assert await api.app.state.storage.head(key) is None  # lo subido se borró del almacén
    actions = {
        r["action"]
        for r in await sql(api, "select action from audit_events where org_id = $1", uuid.UUID(org))
    }
    assert "upload.rejected" in actions


async def test_abort_discards_everything(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    started = await _start(ana, org)
    up, asset = started["upload"], started["asset"]
    await _put_parts(ana, org, up["id"], {1: PART})
    assert (await ana.call("POST", f"/assets/uploads/{up['id']}/abort", org=org)).status_code == 204
    assert (await ana.call("GET", f"/assets/{asset['id']}", org=org)).status_code == 404
    assert (await ana.call("GET", f"/assets/uploads/{up['id']}", org=org)).status_code == 404
    assert (await ana.call("GET", "/assets", org=org)).json()["items"] == []
    # el multipart del almacén desapareció: ya no se puede completar ni listar
    assert (
        await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    ).status_code == 404


# ------------------------------------------------------------------------- validación


async def test_start_validation(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)

    async def start(**over: Any) -> httpx.Response:
        return await ana.call("POST", "/assets/uploads", org=org, json=_body(**over))

    no_rights = await start(rights_confirmed=False)
    assert no_rights.status_code == 422 and no_rights.json()["code"] == "rights_not_confirmed"
    missing = _body()
    del missing["rights_confirmed"]
    assert (await ana.call("POST", "/assets/uploads", org=org, json=missing)).status_code == 422
    assert (await start(size=0)).status_code == 422
    too_big = await start(size=api.settings.upload_max_bytes + 1)
    assert too_big.status_code == 413 and too_big.json()["code"] == "upload_too_large"
    exe = await start(filename="virus.exe", content_type="application/x-msdownload")
    assert exe.status_code == 415 and exe.json()["code"] == "unsupported_media_type"
    assert (await start(filename="../..")).status_code == 422  # sin nombre usable
    assert (await start(sha256="zz")).status_code == 422
    assert (await start(project_id=str(uuid.uuid4()))).status_code == 404
    assert (
        await ana.call("POST", "/assets/uploads", org=org, json={**_body(), "x": 1})
    ).status_code == 422
    # nada de lo rechazado dejó assets ni subidas
    assert (await ana.call("GET", "/assets", org=org)).json()["items"] == []

    traversal = await start(filename="../../etc/passwd.mp4")
    assert traversal.status_code == 201
    assert traversal.json()["asset"]["title"] == "passwd"  # el título sale del nombre limpio


async def test_part_url_validation_and_expiry(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    up = (await _start(ana, org))["upload"]

    async def parts(numbers: list[int]) -> httpx.Response:
        return await ana.call(
            "POST", f"/assets/uploads/{up['id']}/parts", org=org, json={"part_numbers": numbers}
        )

    assert (await parts([0])).status_code == 422
    assert (await parts([4])).status_code == 422  # solo hay 3 partes
    assert (await parts([])).status_code == 422
    assert (await parts(list(range(1, 102)))).status_code == 422
    ok = await parts([2, 1, 2])
    assert (
        sorted(ok.json()["urls"]) == ["1", "2"]
        and 0 < ok.json()["expires_in"] <= api.settings.storage_url_ttl_s
    )

    await sql(
        api,
        "update uploads set expires_at = now() - interval '1 minute' where id = $1",
        uuid.UUID(up["id"]),
    )
    expired = await parts([1])
    assert expired.status_code == 409 and expired.json()["code"] == "upload_expired"


async def test_open_uploads_are_limited_per_organization(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    for _ in range(20):
        await _start(ana, org, size=10)
    blocked = await ana.call("POST", "/assets/uploads", org=org, json=_body(size=10))
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    other, org2 = await _setup(api, users, "otra@example.com")
    assert (
        await other.call("POST", "/assets/uploads", org=org2, json=_body(size=10))
    ).status_code == 201


async def test_idempotent_start(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    headers = {"Idempotency-Key": "subida-1"}
    first = await ana.call("POST", "/assets/uploads", org=org, json=_body(), headers=headers)
    second = await ana.call("POST", "/assets/uploads", org=org, json=_body(), headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.headers.get("idempotent-replayed") == "true"
    assert first.json()["upload"]["id"] == second.json()["upload"]["id"]
    other = await ana.call(
        "POST", "/assets/uploads", org=org, json=_body(size=12 * MIB), headers=headers
    )
    assert other.status_code == 409 and other.json()["code"] == "idempotency_key_reuse"
    count = (await sql(api, "select count(*) as n from uploads where org_id = $1", uuid.UUID(org)))[
        0
    ]["n"]
    assert count == 1  # el reintento no creó una segunda subida


# ---------------------------------------------------------------------- deduplicación


async def test_deduplication_by_verified_sha256(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    org_id = uuid.UUID(org)
    started = await _start(ana, org, size=PART + 10)
    up, first_asset = started["upload"], started["asset"]
    await _put_parts(ana, org, up["id"], {1: PART, 2: 10})
    assert (
        await ana.call("POST", f"/assets/uploads/{up['id']}/complete", org=org)
    ).status_code == 200

    digest = "ab" * 32
    # un hash que el cliente DECLARA pero nadie verificó no deduplica nada
    claimed = await _start(ana, org, size=PART + 10, sha256=digest)
    assert claimed["deduplicated"] is False and claimed["upload"] is not None
    await ana.call("POST", f"/assets/uploads/{claimed['upload']['id']}/abort", org=org)

    # el análisis (F1-04) verifica el hash y deja el asset listo
    media = api.app.state.media_service
    await media.transition_asset(
        org_id, uuid.UUID(first_asset["id"]), "ready", duration_ms=90_000, width=1280, height=720
    )
    await sql(
        api,
        "update storage_objects set sha256 = $2 where id = (select storage_object_id from media_assets where id = $1)",
        uuid.UUID(first_asset["id"]),
        bytes.fromhex(digest),
    )
    dup = await ana.call(
        "POST", "/assets/uploads", org=org, json=_body(size=PART + 10, sha256=digest, title="Copia")
    )
    assert dup.status_code == 201, dup.text
    body = dup.json()
    assert body["deduplicated"] is True and body["upload"] is None
    assert body["asset"]["status"] == "ready" and body["asset"]["title"] == "Copia"
    assert (body["asset"]["duration_ms"], body["asset"]["width"]) == (90_000, 1280)
    assert body["asset"]["id"] != first_asset["id"]

    rows = await sql(
        api,
        "select count(distinct storage_object_id) as objs, count(*) as assets, "
        "(select max(ref_count) from storage_objects where org_id = $1) as refs "
        "from media_assets where org_id = $1",
        org_id,
    )
    assert (rows[0]["objs"], rows[0]["assets"], rows[0]["refs"]) == (
        1,
        2,
        2,
    )  # un objeto, dos assets
    # borrar uno de los dos (borrado definitivo) deja el objeto vivo para el otro
    await sql(api, "delete from media_assets where id = $1", uuid.UUID(body["asset"]["id"]))
    assert (
        await sql(api, "select ref_count, status from storage_objects where org_id = $1", org_id)
    )[0]["status"] == "ready"

    # otra organización con el mismo hash no ve ni reutiliza el contenido ajeno
    bob, org_b = await _setup(api, users, "bob@example.com")
    foreign = await bob.call(
        "POST", "/assets/uploads", org=org_b, json=_body(size=PART + 10, sha256=digest)
    )
    assert foreign.json()["deduplicated"] is False


# --------------------------------------------------------------- permisos y aislamiento


async def test_permissions_and_cross_organization_access(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    viewer = await users.make("viewer@example.com")
    await add_member(api, ana, org, viewer, "viewer")
    denied = await viewer.call("POST", "/assets/uploads", org=org, json=_body())
    assert denied.status_code == 403
    up = (await _start(ana, org))["upload"]
    for method, path, body in [
        ("GET", f"/assets/uploads/{up['id']}", None),
        ("POST", f"/assets/uploads/{up['id']}/parts", {"part_numbers": [1]}),
        ("POST", f"/assets/uploads/{up['id']}/complete", None),
        ("POST", f"/assets/uploads/{up['id']}/abort", None),
    ]:
        assert (await viewer.call(method, path, org=org, json=body)).status_code == 403, path

    bob, org_b = await _setup(api, users, "bob@example.com")
    for method, path, body in [
        ("GET", f"/assets/uploads/{up['id']}", None),
        ("POST", f"/assets/uploads/{up['id']}/parts", {"part_numbers": [1]}),
        ("POST", f"/assets/uploads/{up['id']}/complete", None),
        ("POST", f"/assets/uploads/{up['id']}/abort", None),
    ]:
        assert (await bob.call(method, path, org=org_b, json=body)).status_code == 404, path
    # la subida de ana sigue intacta
    assert (await ana.call("GET", f"/assets/uploads/{up['id']}", org=org)).json()[
        "status"
    ] == "open"


# -------------------------------------------------------------------- recolector


async def test_reaper_aborts_expired_uploads_only(api: ApiHarness, users: Users) -> None:
    ana, org = await _setup(api, users)
    stale = await _start(ana, org)
    fresh = await _start(ana, org)
    await _put_parts(ana, org, stale["upload"]["id"], {1: PART})
    await sql(
        api,
        "update uploads set expires_at = $2 where id = $1",
        uuid.UUID(stale["upload"]["id"]),
        datetime.now(UTC) - timedelta(minutes=5),
    )
    assert await reap_expired_uploads(api.app.state.db, api.app.state.storage) == 1
    assert (await ana.call("GET", f"/assets/{stale['asset']['id']}", org=org)).status_code == 404
    assert (await ana.call("GET", f"/assets/{fresh['asset']['id']}", org=org)).status_code == 200
    assert (await ana.call("GET", f"/assets/uploads/{fresh['upload']['id']}", org=org)).json()[
        "status"
    ] == "open"
    assert await reap_expired_uploads(api.app.state.db, api.app.state.storage) == 0  # es repetible
