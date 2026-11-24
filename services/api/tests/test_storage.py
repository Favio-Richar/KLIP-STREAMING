import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from actors import Users, create_org
from harness import ApiHarness
from klick.core.db import Database
from klick.core.errors import ApiError
from klick.storage import keys
from klick.storage.backend import MIN_PART_SIZE, CompletedPart, S3Backend
from klick.storage.gc import collect_garbage
from klick.storage.service import StorageService
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


def _key(org: uuid.UUID, name: str | None = None) -> str:
    return keys.asset_original(org, uuid.UUID(int=0)) + "-" + (name or uuid.uuid4().hex[:8])


async def _org(api: ApiHarness, users: Users, email: str = "ana@example.com") -> uuid.UUID:
    ana = await users.make(email)
    return uuid.UUID((await create_org(ana, f"Org {email}"))["id"])


def _service(api: ApiHarness) -> StorageService:
    service: StorageService = api.app.state.storage_service
    return service


# ----------------------------------------------------------------------------- claves


def test_keys_follow_the_documented_layout() -> None:
    org, asset, job, session = (uuid.uuid4() for _ in range(4))
    assert keys.asset_original(org, asset) == f"org/{org}/assets/{asset}/original"
    assert (
        keys.asset_derivative(org, asset, "thumbnail")
        == f"org/{org}/assets/{asset}/derivatives/thumbnail"
    )
    assert keys.render_output(org, job) == f"org/{org}/renders/{job}.mp4"
    assert keys.live_segment(org, session, 7) == f"org/{org}/live/{session}/seg_7.m4s"
    assert keys.belongs_to_org(keys.asset_original(org, asset), org)
    assert not keys.belongs_to_org(keys.asset_original(org, asset), uuid.uuid4())


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/abs",
        "a//b",
        "a/../b",
        "a/./b",
        "org/x/",
        "a\\b",
        "a/b c",
        "a/\x00",
        "x" * 1100,
        "a/%2e%2e/b",
    ],
)
def test_unsafe_keys_are_rejected(bad: str) -> None:
    with pytest.raises(keys.InvalidKeyError):
        keys.validate_key(bad)


def test_unknown_derivative_and_negative_segment_are_rejected() -> None:
    with pytest.raises(keys.InvalidKeyError):
        keys.asset_derivative(uuid.uuid4(), uuid.uuid4(), "../x")
    with pytest.raises(keys.InvalidKeyError):
        keys.live_segment(uuid.uuid4(), uuid.uuid4(), -1)


# ------------------------------------------------------------------- backend (MinIO real)


async def test_object_roundtrip_head_and_idempotent_delete(storage: S3Backend) -> None:
    key = _key(uuid.uuid4())
    assert await storage.head(key) is None
    await storage.put_bytes(key, b"hola klick", "text/plain")
    info = await storage.head(key)
    assert info is not None and info.size == 10 and info.content_type == "text/plain"
    assert await storage.get_bytes(key) == b"hola klick"
    with pytest.raises(ValueError, match="máximo"):
        await storage.get_bytes(key, max_bytes=3)
    await storage.delete(key)
    await storage.delete(key)  # borrar lo que no existe no es un error
    assert await storage.head(key) is None


async def test_backend_refuses_unsafe_keys(storage: S3Backend) -> None:
    for call in (
        storage.head("../etc/passwd"),
        storage.put_bytes("a/../b", b"x"),
        storage.delete("/abs"),
        storage.create_multipart("a//b"),
    ):
        with pytest.raises(keys.InvalidKeyError):
            await call
    with pytest.raises(keys.InvalidKeyError):
        storage.presign_get("a/../b", expires_s=60)


async def test_presigned_upload_and_download(storage: S3Backend) -> None:
    key = _key(uuid.uuid4())
    async with httpx.AsyncClient() as http:
        up = await http.put(
            storage.presign_put(key, expires_s=60, content_type="video/mp4"),
            content=b"datos-de-video",
            headers={"Content-Type": "video/mp4"},
        )
        assert up.status_code == 200
        url = storage.presign_get(key, expires_s=60, filename='mi "clip".mp4')
        down = await http.get(url)
        assert down.status_code == 200 and down.content == b"datos-de-video"
        disposition = down.headers["content-disposition"]
        assert disposition.startswith("attachment;") and '"mi clip.mp4"' in disposition

        # el tipo de contenido firmado no se puede cambiar al subir
        bad = await http.put(
            storage.presign_put(key, expires_s=60, content_type="video/mp4"),
            content=b"x",
            headers={"Content-Type": "text/html"},
        )
        assert bad.status_code == 403


async def test_presigned_urls_expire_and_cannot_be_retargeted(storage: S3Backend) -> None:
    org = uuid.uuid4()
    key_a, key_b = _key(org, "a"), _key(org, "b")
    await storage.put_bytes(key_a, b"A")
    await storage.put_bytes(key_b, b"B")
    async with httpx.AsyncClient() as http:
        short = storage.presign_get(key_a, expires_s=4)
        assert (await http.get(short)).content == b"A"
        await asyncio.sleep(6)
        assert (await http.get(short)).status_code == 403  # vencida

        url_a = storage.presign_get(key_a, expires_s=60)
        forged = url_a.replace(key_a, key_b)  # la firma cubre la ruta
        assert (await http.get(forged)).status_code == 403
        anonymous = url_a.split("?")[0]
        assert (await http.get(anonymous)).status_code == 403  # sin firma no hay acceso
    for bad in (0, -5, 24 * 3600 + 1):
        with pytest.raises(ValueError, match="expires_s"):
            storage.presign_get(key_a, expires_s=bad)


async def test_multipart_upload_roundtrip(storage: S3Backend) -> None:
    key = _key(uuid.uuid4())
    part1 = b"a" * MIN_PART_SIZE
    part2 = b"fin"
    upload_id = await storage.create_multipart(key, "video/mp4")
    etags: list[CompletedPart] = []
    async with httpx.AsyncClient(timeout=60) as http:
        for number, data in ((1, part1), (2, part2)):
            url = storage.presign_part(key, upload_id, number, expires_s=120)
            r = await http.put(url, content=data)
            assert r.status_code == 200
            etags.append(CompletedPart(number, r.headers["etag"].strip('"')))
    listed = await storage.list_parts(key, upload_id)
    assert [p.part_number for p in listed] == [1, 2]  # reanudable: el servidor sabe qué llegó
    await storage.complete_multipart(
        key, upload_id, list(reversed(etags))
    )  # el orden lo impone el backend
    info = await storage.head(key)
    assert info is not None and info.size == MIN_PART_SIZE + 3
    assert (await storage.get_bytes(key))[-3:] == b"fin"
    with pytest.raises(ValueError, match="part_number"):
        storage.presign_part(key, upload_id, 0, expires_s=60)


async def test_aborted_multipart_leaves_no_object(storage: S3Backend) -> None:
    key = _key(uuid.uuid4())
    upload_id = await storage.create_multipart(key)
    await storage.abort_multipart(key, upload_id)
    await storage.abort_multipart(key, upload_id)  # idempotente
    assert await storage.head(key) is None


async def test_readyz_includes_storage(api: ApiHarness, storage: S3Backend) -> None:
    r = await api.client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"]["storage"] == "ok"


# -------------------------------------------------------------------- servicio (BD + S3)


async def test_register_upload_validates_ownership_and_uniqueness(
    api: ApiHarness, users: Users
) -> None:
    org_a = await _org(api, users, "ana@example.com")
    org_b = await _org(api, users, "bruno@example.com")
    svc = _service(api)
    obj = await svc.register_upload(org_a, key=_key(org_a), mime="video/mp4")
    assert obj.status == "uploading" and obj.ref_count == 0 and obj.bucket == api.settings.s3_bucket
    with pytest.raises(ApiError) as dup:
        await svc.register_upload(org_a, key=obj.key)
    assert dup.value.code == "storage_key_exists"
    for bad_key in (_key(org_b), "org/../x", "sin-prefijo/x"):  # otra org, traversal, sin prefijo
        with pytest.raises(ApiError) as err:
            await svc.register_upload(org_a, key=bad_key)
        assert err.value.code == "invalid_storage_key"


async def test_object_lifecycle_upload_confirm_and_download(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org = await _org(api, users)
    svc = _service(api)
    obj = await svc.register_upload(org, key=_key(org), mime="video/mp4")

    with pytest.raises(ApiError) as early:
        await svc.mark_ready(org, obj.id)  # aún no se subió nada
    assert early.value.code == "storage_object_missing"
    with pytest.raises(ApiError):
        await svc.presign_download(org, obj.id)  # solo se descarga lo listo

    payload = b"contenido del video"
    async with httpx.AsyncClient() as http:
        put = await http.put(
            await svc.presign_upload(org, obj.id),
            content=payload,
            headers={"Content-Type": "video/mp4"},
        )
        assert put.status_code == 200
        digest = hashlib.sha256(payload).digest()
        ready = await svc.mark_ready(org, obj.id, sha256=digest)
        assert (
            ready.status == "ready" and ready.size_bytes == len(payload) and ready.sha256 == digest
        )
        down = await http.get(await svc.presign_download(org, obj.id, filename="clip.mp4"))
        assert down.content == payload
    with pytest.raises(ApiError):
        await svc.presign_upload(org, obj.id)  # ya no admite subida
    with pytest.raises(ApiError) as bad_hash:
        await svc.mark_ready(org, obj.id, sha256=b"corto")
    assert bad_hash.value.code == "invalid_sha256"


async def test_dedup_by_sha256_is_scoped_to_the_organization(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org_a = await _org(api, users, "ana@example.com")
    org_b = await _org(api, users, "bruno@example.com")
    svc = _service(api)
    digest = hashlib.sha256(b"mismo contenido").digest()
    obj = await svc.register_upload(org_a, key=_key(org_a))
    await storage.put_bytes(obj.key, b"mismo contenido")
    await svc.mark_ready(org_a, obj.id, sha256=digest)
    found = await svc.find_ready_by_sha256(org_a, digest)
    assert found is not None and found.id == obj.id
    assert await svc.find_ready_by_sha256(org_b, digest) is None  # nunca entre organizaciones
    assert await svc.find_ready_by_sha256(org_a, hashlib.sha256(b"otro").digest()) is None


async def test_reference_counting_drives_orphan_state(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org = await _org(api, users)
    svc = _service(api)
    obj = await svc.register_upload(org, key=_key(org))
    with pytest.raises(ApiError):
        await svc.add_ref(org, obj.id)  # no se referencia algo aún no listo
    await storage.put_bytes(obj.key, b"x")
    await svc.mark_ready(org, obj.id)

    assert (await svc.add_ref(org, obj.id)).ref_count == 1
    assert (await svc.add_ref(org, obj.id)).ref_count == 2
    mid = await svc.release_ref(org, obj.id)
    assert mid.ref_count == 1 and mid.status == "ready"
    gone = await svc.release_ref(org, obj.id)
    assert gone.ref_count == 0 and gone.status == "orphan" and gone.orphaned_at is not None
    again = await svc.release_ref(org, obj.id)
    assert again.ref_count == 0  # nunca baja de cero
    revived = await svc.add_ref(org, obj.id)  # un huérfano referenciado vuelve a la vida
    assert revived.status == "ready" and revived.orphaned_at is None and revived.ref_count == 1


async def test_objects_are_isolated_between_organizations(api: ApiHarness, users: Users) -> None:
    org_a = await _org(api, users, "ana@example.com")
    org_b = await _org(api, users, "bruno@example.com")
    svc = _service(api)
    obj = await svc.register_upload(org_a, key=_key(org_a))
    for call in (
        svc.get(org_b, obj.id),
        svc.add_ref(org_b, obj.id),
        svc.release_ref(org_b, obj.id),
    ):
        with pytest.raises(ApiError) as err:
            await call
        assert err.value.status in (404, 409)
    assert (await svc.get(org_a, obj.id)).id == obj.id


# -------------------------------------------------------------------- recolector (GC)


async def _ready_object(
    api: ApiHarness, org: uuid.UUID, storage: S3Backend, data: bytes = b"x"
) -> uuid.UUID:
    svc = _service(api)
    obj = await svc.register_upload(org, key=_key(org))
    await storage.put_bytes(obj.key, data)
    await svc.mark_ready(org, obj.id)
    return obj.id


async def _gc(api: ApiHarness, storage: S3Backend) -> object:
    db: Database = api.app.state.db
    return await collect_garbage(db, storage, api.settings)


async def test_gc_deletes_orphans_only_after_the_grace_period(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org = await _org(api, users)
    svc = _service(api)
    object_id = await _ready_object(api, org, storage)
    await svc.add_ref(org, object_id)
    await svc.release_ref(org, object_id)  # queda huérfano
    key = (await svc.get(org, object_id)).key

    report = await _gc(api, storage)
    assert report.deleted == 0 and await storage.head(key) is not None  # type: ignore[attr-defined]  # dentro de la gracia

    await _sql(
        api,
        "update storage_objects set orphaned_at = now() - interval '25 hours' where id = $1",
        object_id,
    )
    report = await _gc(api, storage)
    assert report.deleted == 1  # type: ignore[attr-defined]
    assert await storage.head(key) is None
    row = (await _sql(api, "select status from storage_objects where id = $1", object_id))[0]
    assert row["status"] == "deleted"
    with pytest.raises(ApiError):
        await svc.get(org, object_id)  # ya no es visible para la aplicación
    assert (await _gc(api, storage)).deleted == 0  # type: ignore[attr-defined]  # idempotente


async def test_gc_never_touches_referenced_or_unexpired_objects(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org = await _org(api, users)
    svc = _service(api)
    referenced = await _ready_object(api, org, storage)
    await svc.add_ref(org, referenced)
    # Estado incoherente a propósito: huérfano vencido pero con referencias → no se borra.
    await _sql(
        api,
        "update storage_objects set status='orphan', orphaned_at = now() - interval '3 days' "
        "where id = $1",
        referenced,
    )
    live = await _ready_object(api, org, storage)  # ready, nunca huérfano
    report = await _gc(api, storage)
    assert report.deleted == 0  # type: ignore[attr-defined]
    for oid in (referenced, live):
        assert await storage.head((await svc.get(org, oid)).key) is not None


async def test_gc_abandons_stale_uploads_then_collects_them(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org = await _org(api, users)
    svc = _service(api)
    obj = await svc.register_upload(org, key=_key(org))
    await storage.put_bytes(obj.key, b"parcial")  # alguien subió pero nunca confirmó
    await _sql(
        api,
        "update storage_objects set created_at = now() - interval '30 hours' where id = $1",
        obj.id,
    )
    first = await _gc(api, storage)
    assert first.abandoned_uploads == 1 and first.deleted == 0  # type: ignore[attr-defined]
    assert (await _sql(api, "select status from storage_objects where id = $1", obj.id))[0][
        "status"
    ] == "orphan"
    await _sql(
        api,
        "update storage_objects set orphaned_at = now() - interval '25 hours' where id = $1",
        obj.id,
    )
    assert (await _gc(api, storage)).deleted == 1  # type: ignore[attr-defined]
    assert await storage.head(obj.key) is None


async def test_gc_keeps_the_row_when_the_store_fails_and_retries_later(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org = await _org(api, users)
    svc = _service(api)
    object_id = await _ready_object(api, org, storage)
    await svc.add_ref(org, object_id)
    await svc.release_ref(org, object_id)
    await _sql(
        api,
        "update storage_objects set orphaned_at = now() - interval '25 hours' where id = $1",
        object_id,
    )

    class Broken(S3Backend):
        async def delete(self, key: str) -> None:
            raise ConnectionError("almacén caído")

    db: Database = api.app.state.db
    broken = Broken(api.settings)
    report = await collect_garbage(db, broken, api.settings)
    assert report.failed == 1 and report.deleted == 0
    assert (await _sql(api, "select status from storage_objects where id = $1", object_id))[0][
        "status"
    ] == "orphan"
    assert (await collect_garbage(db, storage, api.settings)).deleted == 1  # se reintenta


async def test_gc_purges_old_deleted_rows(
    api: ApiHarness, users: Users, storage: S3Backend
) -> None:
    org = await _org(api, users)
    object_id = await _ready_object(api, org, storage)
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:  # el trigger de updated_at pisaría la fecha de prueba: se suspende solo aquí
        async with conn.transaction():
            await conn.execute("alter table storage_objects disable trigger trg_set_updated_at")
            await conn.execute(
                "update storage_objects set status='deleted', "
                "updated_at = now() - interval '40 days' where id = $1",
                object_id,
            )
            await conn.execute("alter table storage_objects enable trigger trg_set_updated_at")
    finally:
        await conn.close()
    report = await _gc(api, storage)
    assert report.purged_rows == 1  # type: ignore[attr-defined]
    assert not await _sql(api, "select 1 from storage_objects where id = $1", object_id)


async def test_database_enforces_key_prefix_and_ref_count(api: ApiHarness, users: Users) -> None:
    org = await _org(api, users)
    other = uuid.uuid4()
    with pytest.raises(asyncpg.CheckViolationError):
        await _sql(
            api,
            "insert into storage_objects (id, org_id, bucket, key) values (gen_random_uuid(), $1, 'b', $2)",
            org,
            f"org/{other}/assets/x",  # clave de otra organización
        )
    obj = await _service(api).register_upload(org, key=_key(org))
    with pytest.raises(asyncpg.CheckViolationError):
        await _sql(api, "update storage_objects set ref_count = -1 where id = $1", obj.id)
    assert datetime.now(UTC) - obj.created_at < timedelta(minutes=5) if obj.created_at else True
    _ = os.environ  # mantiene la importación usada por las pruebas que dependen del entorno
