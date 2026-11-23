import uuid

import asyncpg
import pytest
from actors import (
    BASE,
    Users,
    _client,
    add_member,
    create_org,
    invite_token,
)
from harness import ApiHarness
from klick.core.config import Settings
from klick.core.db import Database
from klick.core.seed import seed_permissions_and_roles
from klick.orgs.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS
from sqlalchemy import text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db

# Para invitar a un viewer, el token debe tener también los permisos que ese rol otorga.
VIEWER_GRANT_SCOPES = ["members:manage", "media:read", "clips:read", "analytics:read"]


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


# ------------------------------------------------------------------------------- semillas


async def test_seeded_roles_match_the_documented_matrix(api: ApiHarness) -> None:
    rows = await _sql(
        api,
        "select r.key, array_agg(rp.permission_key) as perms from roles r "
        "join role_permissions rp on rp.role_id = r.id where r.org_id is null group by r.key",
    )
    seeded = {r["key"]: frozenset(r["perms"]) for r in rows}
    assert seeded == dict(ROLE_PERMISSIONS)
    assert seeded["owner"] == ALL_PERMISSIONS
    assert "billing:manage" not in seeded["admin"]
    assert "members:manage" not in seeded["editor"] | seeded["reviewer"] | seeded["viewer"]
    perms = await _sql(api, "select count(*) as n from permissions")
    assert perms[0]["n"] == len(ALL_PERMISSIONS)


async def test_seed_is_idempotent(api: ApiHarness) -> None:
    db = Database(Settings(database_url=api.db_urls["system"]))
    try:
        for _ in range(2):
            async with db.global_session() as s:
                await seed_permissions_and_roles(s)
    finally:
        await db.dispose()
    assert (await _sql(api, "select count(*) as n from roles where org_id is null"))[0]["n"] == 5
    assert (await _sql(api, "select count(*) as n from role_permissions"))[0]["n"] == sum(
        len(p) for p in ROLE_PERMISSIONS.values()
    )


async def test_app_role_cannot_modify_permission_catalog(api: ApiHarness) -> None:
    db = Database(Settings(database_url=api.db_urls["app"]))
    try:
        with pytest.raises(Exception, match="permission denied"):
            async with db.global_session() as s:
                await s.execute(text("delete from permissions"))
        with pytest.raises(Exception, match="(permission denied|row-level security)"):
            async with db.global_session() as s:
                await s.execute(
                    text(
                        "insert into roles (id, org_id, key, name) values (gen_random_uuid(), null, 'x', 'x')"
                    )
                )
    finally:
        await db.dispose()


# ------------------------------------------------------------------------ organizaciones


async def test_create_org_makes_creator_owner(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    org = await create_org(ana, "Visionary AI Group Ñandú")
    assert org["slug"] == "visionary-ai-group-nandu"
    assert org["role"] == "owner"
    assert set(org["permissions"]) == ALL_PERMISSIONS
    listed = (await ana.call("GET", "/orgs")).json()
    assert [o["id"] for o in listed] == [org["id"]]
    members = (await ana.call("GET", "/members", org=org["id"])).json()
    assert len(members) == 1 and members[0]["role"] == "owner" and members[0]["email"] == ana.email


async def test_slugs_are_unique(api: ApiHarness, users: Users) -> None:
    ana, bruno = await users.make("ana@example.com"), await users.make("bruno@example.com")
    first = await create_org(ana, "Mi Canal")
    second = await create_org(bruno, "Mi Canal")  # el nombre repetido recibe un sufijo
    assert first["slug"] == "mi-canal" and second["slug"].startswith("mi-canal-")
    dup = await bruno.call("POST", "/orgs", json={"name": "Otro", "slug": "mi-canal"})
    assert dup.status_code == 409 and dup.json()["code"] == "slug_taken"
    bad = await bruno.call("POST", "/orgs", json={"name": "Otro", "slug": "Mal Slug!"})
    assert bad.status_code == 422


async def test_orgs_are_isolated_between_users(api: ApiHarness, users: Users) -> None:
    ana, bruno = await users.make("ana@example.com"), await users.make("bruno@example.com")
    org_a, org_b = await create_org(ana, "Org A"), await create_org(bruno, "Org B")
    assert [o["id"] for o in (await ana.call("GET", "/orgs")).json()] == [org_a["id"]]
    for method, path, org in (
        ("GET", f"/orgs/{org_b['id']}", None),
        ("PATCH", f"/orgs/{org_b['id']}", None),
        ("GET", "/members", org_b["id"]),
        ("GET", "/invitations", org_b["id"]),
    ):
        r = await ana.call(method, path, org=org, json={"name": "x"} if method == "PATCH" else None)
        assert r.status_code == 403, (method, path, r.text)
    ghost = await ana.call("GET", "/members", org=uuid.uuid4())
    assert ghost.status_code == 403  # igual que una organización ajena: sin oráculo de existencia
    assert (await ana.call("GET", f"/orgs/{org_a['id']}")).status_code == 200


async def test_org_header_is_required_and_validated(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    await create_org(ana)
    missing = await ana.call("GET", "/members")
    assert missing.status_code == 400 and missing.json()["code"] == "org_required"
    invalid = await ana.call("GET", "/members", org="not-a-uuid")
    assert invalid.status_code == 400 and invalid.json()["code"] == "invalid_org_id"
    anon = await users.api.client.get(f"{BASE}/members", headers={"X-Org-ID": str(uuid.uuid4())})
    assert anon.status_code == 401


async def test_update_org_validates_and_requires_permission(api: ApiHarness, users: Users) -> None:
    ana, vic = await users.make("ana@example.com"), await users.make("vic@example.com")
    org = await create_org(ana)
    await add_member(api, ana, org["id"], vic, "viewer")
    ok = await ana.call(
        "PATCH",
        f"/orgs/{org['id']}",
        json={"name": "Nuevo nombre", "settings": {"locale": "en", "timezone": "America/Santiago"}},
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "Nuevo nombre" and ok.json()["settings"]["locale"] == "en"
    assert (
        await ana.call("PATCH", f"/orgs/{org['id']}", json={"settings": {"timezone": "Mars/Base"}})
    ).status_code == 422
    assert (await ana.call("PATCH", f"/orgs/{org['id']}", json={"plan_id": "x"})).status_code == 422
    denied = await vic.call("PATCH", f"/orgs/{org['id']}", json={"name": "Hackeado"})
    assert denied.status_code == 403 and denied.json()["code"] == "forbidden"
    assert (await vic.call("GET", f"/orgs/{org['id']}")).json()["name"] == "Nuevo nombre"


# ------------------------------------------------------- invitaciones (flujo de aceptación F0)


async def test_invited_viewer_can_read_but_not_write(api: ApiHarness, users: Users) -> None:
    ana, vic = await users.make("ana@example.com"), await users.make("vic@example.com")
    org = await create_org(ana)
    inv = await ana.call(
        "POST", "/invitations", org=org["id"], json={"email": "VIC@example.com", "role": "viewer"}
    )
    assert inv.status_code == 201 and inv.json()["email"] == "vic@example.com"
    assert "invitations/accept?token=" in api.emails.sent[-1].body
    token = invite_token(api, vic.email)

    accepted = await vic.call("POST", f"/invitations/{token}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "viewer"
    assert set(accepted.json()["permissions"]) == ROLE_PERMISSIONS["viewer"]

    members = await vic.call("GET", "/members", org=org["id"])
    assert members.status_code == 200 and len(members.json()) == 2
    for method, path, body in (
        ("POST", "/invitations", {"email": "x@example.com", "role": "viewer"}),
        ("GET", "/invitations", None),
        ("PATCH", f"/orgs/{org['id']}", {"name": "x"}),
        ("PATCH", f"/members/{members.json()[0]['id']}", {"role": "viewer"}),
        ("DELETE", f"/members/{members.json()[0]['id']}", None),
        ("POST", "/me/api-tokens", {"name": "t", "scopes": ["clips:write"]}),
    ):
        r = await vic.call(method, path, org=org["id"], json=body)
        assert r.status_code == 403, (method, path, r.text)


async def test_invitation_rules(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    vic = await users.make("vic@example.com")
    ghost = await users.make("sinverificar@example.com", verify=False)
    eve = await users.make("eve@example.com")
    org = await create_org(ana)

    await ana.call(
        "POST", "/invitations", org=org["id"], json={"email": vic.email, "role": "viewer"}
    )
    first = invite_token(api, vic.email)
    # reinvitar revoca la anterior
    await ana.call(
        "POST", "/invitations", org=org["id"], json={"email": vic.email, "role": "editor"}
    )
    second = invite_token(api, vic.email)
    assert first != second
    assert (await vic.call("POST", f"/invitations/{first}/accept")).status_code == 400
    # otra persona con el token no puede usarlo
    assert (await eve.call("POST", f"/invitations/{second}/accept")).status_code == 403
    pending = (await ana.call("GET", "/invitations", org=org["id"])).json()
    assert len(pending) == 1 and pending[0]["role"] == "editor"
    # correo sin verificar
    await ana.call(
        "POST", "/invitations", org=org["id"], json={"email": ghost.email, "role": "viewer"}
    )
    assert (
        await ghost.call("POST", f"/invitations/{invite_token(api, ghost.email)}/accept")
    ).status_code == 403

    ok = await vic.call("POST", f"/invitations/{second}/accept")
    assert ok.status_code == 200 and ok.json()["role"] == "editor"
    # un solo uso
    assert (await vic.call("POST", f"/invitations/{second}/accept")).status_code == 400
    # ya es miembro
    again = await ana.call(
        "POST", "/invitations", org=org["id"], json={"email": vic.email, "role": "viewer"}
    )
    assert again.status_code == 409 and again.json()["code"] == "already_member"
    # token desconocido
    assert (await vic.call("POST", f"/invitations/{'z' * 43}/accept")).status_code == 400


async def test_invitation_can_be_revoked_and_expires(api: ApiHarness, users: Users) -> None:
    ana, vic = await users.make("ana@example.com"), await users.make("vic@example.com")
    org = await create_org(ana)
    inv = (
        await ana.call(
            "POST", "/invitations", org=org["id"], json={"email": vic.email, "role": "viewer"}
        )
    ).json()
    token = invite_token(api, vic.email)
    assert (await ana.call("DELETE", f"/invitations/{inv['id']}", org=org["id"])).status_code == 204
    assert (await vic.call("POST", f"/invitations/{token}/accept")).status_code == 400

    await ana.call(
        "POST", "/invitations", org=org["id"], json={"email": vic.email, "role": "viewer"}
    )
    token = invite_token(api, vic.email)
    await _sql(api, "update invitations set expires_at = now() - interval '1 minute'")
    assert (await vic.call("POST", f"/invitations/{token}/accept")).status_code == 400


# --------------------------------------------------------------- escalada y último propietario


async def test_nobody_can_grant_more_than_they_have(api: ApiHarness, users: Users) -> None:
    ana = await users.make("ana@example.com")
    adm = await users.make("adm@example.com")
    ed = await users.make("ed@example.com")
    org = await create_org(ana)
    await add_member(api, ana, org["id"], adm, "admin")
    await add_member(api, ana, org["id"], ed, "editor")
    members = {m["email"]: m for m in (await ana.call("GET", "/members", org=org["id"])).json()}

    # un admin no puede crear owners (le falta billing:manage)
    r = await adm.call(
        "POST", "/invitations", org=org["id"], json={"email": "n@example.com", "role": "owner"}
    )
    assert r.status_code == 403
    r = await adm.call(
        "PATCH", f"/members/{members[ed.email]['id']}", org=org["id"], json={"role": "owner"}
    )
    assert r.status_code == 403
    # ni degradar ni expulsar a un owner
    r = await adm.call(
        "PATCH", f"/members/{members[ana.email]['id']}", org=org["id"], json={"role": "viewer"}
    )
    assert r.status_code == 403
    r = await adm.call("DELETE", f"/members/{members[ana.email]['id']}", org=org["id"])
    assert r.status_code == 403
    # sí puede gestionar roles inferiores
    r = await adm.call(
        "PATCH", f"/members/{members[ed.email]['id']}", org=org["id"], json={"role": "reviewer"}
    )
    assert r.status_code == 200 and r.json()["role"] == "reviewer"
    # un editor no gestiona miembros
    r = await ed.call(
        "POST", "/invitations", org=org["id"], json={"email": "n@example.com", "role": "viewer"}
    )
    assert r.status_code == 403
    # ni tokens con permisos que no tiene
    r = await adm.call(
        "POST", "/me/api-tokens", org=org["id"], json={"name": "t", "scopes": ["billing:manage"]}
    )
    assert r.status_code == 403
    r = await ana.call(
        "PATCH", f"/members/{members[ed.email]['id']}", org=org["id"], json={"role": "superuser"}
    )
    assert r.status_code == 422


async def test_last_owner_is_protected(api: ApiHarness, users: Users) -> None:
    ana, bea = await users.make("ana@example.com"), await users.make("bea@example.com")
    org = await create_org(ana)
    me = (await ana.call("GET", "/members", org=org["id"])).json()[0]
    demote = await ana.call("PATCH", f"/members/{me['id']}", org=org["id"], json={"role": "admin"})
    assert demote.status_code == 409 and demote.json()["code"] == "last_owner"
    leave = await ana.call("DELETE", f"/members/{me['id']}", org=org["id"])
    assert leave.status_code == 409

    await add_member(api, ana, org["id"], bea, "owner")
    assert (
        await ana.call("PATCH", f"/members/{me['id']}", org=org["id"], json={"role": "admin"})
    ).status_code == 200
    # ahora Bea es la única owner
    bea_member = next(
        m
        for m in (await bea.call("GET", "/members", org=org["id"])).json()
        if m["email"] == bea.email
    )
    assert (
        await bea.call("DELETE", f"/members/{bea_member['id']}", org=org["id"])
    ).status_code == 409


async def test_member_can_leave_and_loses_access(api: ApiHarness, users: Users) -> None:
    ana, vic = await users.make("ana@example.com"), await users.make("vic@example.com")
    org = await create_org(ana)
    await add_member(api, ana, org["id"], vic, "editor")
    mine = next(
        m
        for m in (await vic.call("GET", "/members", org=org["id"])).json()
        if m["email"] == vic.email
    )
    assert (await vic.call("DELETE", f"/members/{mine['id']}", org=org["id"])).status_code == 204
    assert (await vic.call("GET", "/members", org=org["id"])).status_code == 403
    assert (await vic.call("GET", "/orgs")).json() == []


# ------------------------------------------------------------------------------ tokens API


async def test_api_token_lifecycle_and_scope_limits(api: ApiHarness, users: Users) -> None:
    ana, vic = await users.make("ana@example.com"), await users.make("vic@example.com")
    org = await create_org(ana)
    await add_member(api, ana, org["id"], vic, "viewer")

    created = await ana.call(
        "POST",
        "/me/api-tokens",
        org=org["id"],
        json={"name": "ci", "scopes": VIEWER_GRANT_SCOPES},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    raw = body["token"]
    assert raw.startswith("klk_") and body["prefix"] == raw[:8]
    stored = (await _sql(api, "select token_hash, prefix from api_tokens"))[0]
    assert raw not in stored["token_hash"] and len(stored["token_hash"]) == 64
    listed = (await ana.call("GET", "/me/api-tokens", org=org["id"])).json()
    assert len(listed) == 1 and "token" not in listed[0] and listed[0]["prefix"] == raw[:8]

    # el token funciona sin cookies ni CSRF, y sin X-Org-ID
    async with _client(api) as anon:
        ok = await anon.get(f"{BASE}/members", headers={"Authorization": f"Bearer {raw}"})
        assert ok.status_code == 200
        write = await anon.post(
            f"{BASE}/invitations",
            headers={"Authorization": f"Bearer {raw}"},
            json={"email": "nuevo@example.com", "role": "viewer"},
        )
        assert write.status_code == 201
        # su alcance limita: no tiene clips:write
        assert (
            await anon.post(
                f"{BASE}/me/api-tokens",
                headers={"Authorization": f"Bearer {raw}"},
                json={"name": "otro", "scopes": ["media:read"]},
            )
        ).status_code == 403
        # otra organización en la cabecera: rechazado
        mismatch = await anon.get(
            f"{BASE}/members",
            headers={"Authorization": f"Bearer {raw}", "X-Org-ID": str(uuid.uuid4())},
        )
        assert mismatch.status_code == 403
        for bad in ("klk_" + "x" * 40, "nope", ""):
            r = await anon.get(f"{BASE}/members", headers={"Authorization": f"Bearer {bad}"})
            assert r.status_code == 401, bad
    assert (await _sql(api, "select last_used_at from api_tokens"))[0]["last_used_at"] is not None

    # un viewer no puede crear un token con permisos que no tiene
    r = await vic.call(
        "POST", "/me/api-tokens", org=org["id"], json={"name": "x", "scopes": ["clips:write"]}
    )
    assert r.status_code == 403
    r = await ana.call(
        "POST", "/me/api-tokens", org=org["id"], json={"name": "x", "scopes": ["bogus:scope"]}
    )
    assert r.status_code == 422

    # revocar
    assert (
        await ana.call("DELETE", f"/me/api-tokens/{body['id']}", org=org["id"])
    ).status_code == 204
    async with _client(api) as anon:
        r = await anon.get(f"{BASE}/members", headers={"Authorization": f"Bearer {raw}"})
        assert r.status_code == 401


async def test_api_token_never_exceeds_current_role(api: ApiHarness, users: Users) -> None:
    ana, adm = await users.make("ana@example.com"), await users.make("adm@example.com")
    org = await create_org(ana)
    await add_member(api, ana, org["id"], adm, "admin")
    raw = (
        await adm.call(
            "POST",
            "/me/api-tokens",
            org=org["id"],
            json={"name": "t", "scopes": VIEWER_GRANT_SCOPES},
        )
    ).json()["token"]
    members = {m["email"]: m for m in (await ana.call("GET", "/members", org=org["id"])).json()}
    async with _client(api) as anon:
        auth = {"Authorization": f"Bearer {raw}"}
        assert (
            await anon.post(
                f"{BASE}/invitations",
                headers=auth,
                json={"email": "a@example.com", "role": "viewer"},
            )
        ).status_code == 201
        # Ana degrada a Adm a viewer: el token pierde members:manage aunque conserve el scope
        await ana.call(
            "PATCH", f"/members/{members[adm.email]['id']}", org=org["id"], json={"role": "viewer"}
        )
        assert (
            await anon.post(
                f"{BASE}/invitations",
                headers=auth,
                json={"email": "b@example.com", "role": "viewer"},
            )
        ).status_code == 403
        assert (await anon.get(f"{BASE}/members", headers=auth)).status_code == 200
        # expulsado: el token deja de valer
        await ana.call("DELETE", f"/members/{members[adm.email]['id']}", org=org["id"])
        assert (await anon.get(f"{BASE}/members", headers=auth)).status_code == 401


async def test_api_token_expiry_and_tenant_binding(api: ApiHarness, users: Users) -> None:
    ana, bruno = await users.make("ana@example.com"), await users.make("bruno@example.com")
    org_a, org_b = await create_org(ana, "Org A"), await create_org(bruno, "Org B")
    raw = (
        await ana.call(
            "POST", "/me/api-tokens", org=org_a["id"], json={"name": "t", "scopes": ["media:read"]}
        )
    ).json()["token"]
    async with _client(api) as anon:
        auth = {"Authorization": f"Bearer {raw}"}
        members = (await anon.get(f"{BASE}/members", headers=auth)).json()
        assert [m["email"] for m in members] == [ana.email]  # solo ve su organización
        assert (await anon.get(f"{BASE}/orgs/{org_b['id']}", headers=auth)).status_code == 403
        await _sql(api, "update api_tokens set expires_at = now() - interval '1 second'")
        assert (await anon.get(f"{BASE}/members", headers=auth)).status_code == 401
    # Bruno no puede revocar ni ver el token de Ana
    tid = (await _sql(api, "select id from api_tokens"))[0]["id"]
    assert (await bruno.call("DELETE", f"/me/api-tokens/{tid}", org=org_b["id"])).status_code == 404


# --------------------------------------------------------------------------- RLS directo


async def test_rls_blocks_cross_org_reads_and_writes(api: ApiHarness, users: Users) -> None:
    ana, bruno = await users.make("ana@example.com"), await users.make("bruno@example.com")
    org_a, org_b = await create_org(ana, "Org A"), await create_org(bruno, "Org B")
    await ana.call(
        "POST", "/invitations", org=org_a["id"], json={"email": "x@example.com", "role": "viewer"}
    )
    await bruno.call(
        "POST", "/me/api-tokens", org=org_b["id"], json={"name": "t", "scopes": ["media:read"]}
    )
    a, b = uuid.UUID(org_a["id"]), uuid.UUID(org_b["id"])

    db = Database(Settings(database_url=api.db_urls["app"]))
    try:
        async with db.tenant_session(a) as s:
            for table in ("memberships", "invitations", "api_tokens"):
                rows = (await s.execute(text(f"select org_id from {table}"))).scalars().all()  # noqa: S608
                assert set(rows) <= {a}, table
            assert (await s.execute(text("select id from organizations"))).scalars().all() == [a]
        # sin contexto no se ve nada de negocio
        async with db.global_session() as s:
            for table in ("memberships", "invitations", "api_tokens", "organizations"):
                assert (await s.execute(text(f"select count(*) from {table}"))).scalar_one() == 0  # noqa: S608
        # no se puede escribir en otra organización
        with pytest.raises(Exception, match="row-level security"):
            async with db.tenant_session(a) as s:
                await s.execute(
                    text(
                        "insert into memberships (id, org_id, user_id, role_id) "
                        "select gen_random_uuid(), :o, user_id, role_id from memberships limit 1"
                    ),
                    {"o": b},
                )
        async with db.tenant_session(a) as s:
            res = await s.execute(
                text("update organizations set name = 'x' where id = :b returning id"), {"b": b}
            )
            assert res.all() == []
            res = await s.execute(
                text("delete from memberships where org_id = :b returning id"), {"b": b}
            )
            assert res.all() == []
        # con user_id solo se ven las membresías propias (y nunca las de otros)
        async with db.context_session(user_id=uuid.UUID(ana.user_id)) as s:
            rows = (await s.execute(text("select org_id, user_id from memberships"))).all()
            assert {(r[0], r[1]) for r in rows} == {(a, uuid.UUID(ana.user_id))}
            assert (await s.execute(text("select id from organizations"))).scalars().all() == [a]
        # la lectura de invitaciones/tokens por hash solo devuelve ese registro
        h = (await _sql(api, "select token_hash from invitations"))[0]["token_hash"]
        async with db.context_session(invite_token_hash=h) as s:
            assert (await s.execute(text("select count(*) from invitations"))).scalar_one() == 1
            assert (await s.execute(text("select count(*) from memberships"))).scalar_one() == 0
        async with db.context_session(invite_token_hash="0" * 64) as s:
            assert (await s.execute(text("select count(*) from invitations"))).scalar_one() == 0
    finally:
        await db.dispose()


async def test_context_settings_are_whitelisted(api: ApiHarness) -> None:
    db = Database(Settings(database_url=api.db_urls["app"]))
    try:
        with pytest.raises(ValueError, match="Contexto no permitido"):
            async with db.context_session(**{"role": "postgres"}):
                pass
    finally:
        await db.dispose()
