"""Arnés de IDOR: un usuario de la organización B no toca recursos de la A conociendo sus IDs.

Se recorre POLICY: toda ruta de organización se prueba con (1) la organización ajena en la cabecera
o la ruta y (2) la organización propia pero con IDs de recursos ajenos. Nada puede responder 2xx ni
cambiar el estado de A.
"""

import re
from typing import Any

import httpx
import pytest
from actors import BASE, Users, invite_token
from harness import ApiHarness
from sec_policy import POLICY
from sec_scenario import (
    MEDIA_BODIES,
    ROLE_ORDER,
    Tenant,
    build_tenant,
    media_path_values,
    sql,
)

pytestmark = pytest.mark.db

BODIES: dict[tuple[str, str], Any] = {
    ("PATCH", f"{BASE}/members/{{member_id}}"): {"role": "admin"},
    ("POST", f"{BASE}/invitations"): {"email": "pwn@example.com", "role": "viewer"},
    ("POST", f"{BASE}/me/api-tokens"): {"name": "pwn", "scopes": ["media:read"]},
    ("POST", f"{BASE}/jobs"): {"kind": "system.ping", "payload": {"steps": 1}},
    ("PATCH", f"{BASE}/orgs/{{org_id}}"): {"name": "pwned"},
    **{(m, f"{BASE}{t}"): b for (m, t), b in MEDIA_BODIES.items()},
}


def _path(template: str, a: Tenant, ids: dict[str, str]) -> str:
    values = {
        "member_id": a.member_ids["viewer"],
        "invitation_id": a.invitation_id,
        "token_id": a.token_id,
        "job_id": a.job_id,
        "org_id": a.org_id,
        **media_path_values(a),
        **ids,
    }
    return re.sub(r"\{(\w+)\}", lambda m: values[m.group(1)], template)


async def _snapshot(api: ApiHarness, a: Tenant) -> dict[str, Any]:
    org = a.org_id
    return {
        "members": [
            tuple(r)
            for r in await sql(
                api,
                "select m.id::text, r.key, m.status from memberships m join roles r on r.id = m.role_id "
                "where m.org_id = $1 order by m.id",
                _uuid(org),
            )
        ],
        "invitations": [
            tuple(r)
            for r in await sql(
                api,
                "select id::text, revoked_at, accepted_at from invitations where org_id = $1 order by id",
                _uuid(org),
            )
        ],
        "tokens": [
            tuple(r)
            for r in await sql(
                api,
                "select id::text, revoked_at from api_tokens where org_id = $1 order by id",
                _uuid(org),
            )
        ],
        "org": [
            tuple(r)
            for r in await sql(
                api, "select name, settings::text from organizations where id = $1", _uuid(org)
            )
        ],
        "sessions": (
            await sql(
                api,
                "select count(*) as n from sessions s join memberships m on m.user_id = s.user_id "
                "where m.org_id = $1 and s.revoked_at is null",
                _uuid(org),
            )
        )[0]["n"],
    }


def _uuid(value: str) -> Any:
    import uuid  # noqa: PLC0415

    return uuid.UUID(value)


@pytest.fixture
async def two_tenants(api: ApiHarness, users: Users) -> tuple[Tenant, Tenant]:
    return await build_tenant(api, users, "a"), await build_tenant(api, users, "b")


async def test_foreign_organization_is_never_reachable(
    api: ApiHarness, two_tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = two_tenants
    attacker = b.owner  # propietario de B: todos los permisos, pero en B
    session_id = (await a.owner.call("GET", "/me/sessions")).json()[0]["id"]
    accept_token = invite_token(api, "pending-a@example.com")
    ids = {"session_id": session_id, "token": accept_token}
    before = await _snapshot(api, a)

    checked = 0
    for (method, template), policy in POLICY.items():
        if policy.kind not in ("org_header", "org_path"):
            continue
        body = BODIES.get((method, template), {} if method in {"POST", "PATCH", "PUT"} else None)
        if policy.streaming:
            body = None
        path = _path(template, a, ids)
        short = path.removeprefix(BASE)

        # (1) con la organización A en la cabecera / ruta: nunca autorizado
        r = await attacker.call(method, short, org=a.org_id, json=body)
        assert r.status_code == 403, (
            f"{method} {template} con org ajena respondió {r.status_code}: {r.text}"
        )

        # (2) con la organización propia pero IDs de recursos ajenos: nada se resuelve
        if "{" in template and policy.kind == "org_header":
            r = await attacker.call(method, short, org=b.org_id, json=body)
            assert r.status_code in (
                403,
                404,
            ), f"{method} {template}: IDOR ({r.status_code}) {r.text}"
        checked += 1
    assert checked >= 10

    # rutas de usuario con IDs ajenos
    r = await attacker.call("DELETE", f"/me/sessions/{session_id}")
    assert r.status_code == 404
    r = await attacker.call("POST", f"/invitations/{accept_token}/accept")
    assert r.status_code in (400, 403)  # el correo de la invitación no es el suyo

    assert await _snapshot(api, a) == before, "El estado de la organización A cambió"


async def test_attacker_listings_only_contain_own_data(
    api: ApiHarness, two_tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = two_tenants
    emails = {m["email"] for m in (await b.owner.call("GET", "/members", org=b.org_id)).json()}
    assert emails and all(e.endswith("-b@example.com") for e in emails)
    orgs = (await b.owner.call("GET", "/orgs")).json()
    assert [o["id"] for o in orgs] == [b.org_id]
    invs = (await b.owner.call("GET", "/invitations", org=b.org_id)).json()
    assert all(i["email"].endswith("-b@example.com") for i in invs)
    toks = (await b.owner.call("GET", "/me/api-tokens", org=b.org_id)).json()
    assert [t["id"] for t in toks] == [b.token_id]


async def test_api_tokens_are_bound_to_their_organization(
    api: ApiHarness, two_tenants: tuple[Tenant, Tenant]
) -> None:
    a, b = two_tenants
    auth = {"Authorization": f"Bearer {a.token_raw}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as anon:
        own = await anon.get(f"{BASE}/members", headers=auth)
        assert own.status_code == 200
        assert all(m["email"].endswith("-a@example.com") for m in own.json())
        assert (
            await anon.get(f"{BASE}/members", headers={**auth, "X-Org-ID": b.org_id})
        ).status_code == 403
        assert (await anon.get(f"{BASE}/orgs/{b.org_id}", headers=auth)).status_code == 403
        assert (await anon.get(f"{BASE}/orgs/{a.org_id}", headers=auth)).status_code == 200


async def test_roles_do_not_leak_across_organizations(
    api: ApiHarness, two_tenants: tuple[Tenant, Tenant]
) -> None:
    """Ser owner en B no da ningún permiso en A, ni siquiera para lectura."""
    a, b = two_tenants
    for role in ROLE_ORDER:
        actor = b.actor(role)
        r = await actor.call("GET", "/members", org=a.org_id)
        assert r.status_code == 403, role
