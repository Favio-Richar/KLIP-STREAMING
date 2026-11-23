"""Matriz rol × endpoint: cada ruta de organización se llama con cada rol.

El resultado esperado sale de ``ROLE_PERMISSIONS`` y del permiso declarado en ``POLICY``: sin permiso
debe ser exactamente 403; con permiso, cualquier cosa menos 403/401/5xx (la acción puede dar 404 o 409
según el estado, pero la autorización pasó).
"""

import re
import uuid
from typing import Any

import httpx
import pytest
from actors import BASE, Users
from harness import ApiHarness
from klick.orgs.permissions import ROLE_PERMISSIONS
from sec_policy import POLICY, Policy
from sec_scenario import ROLE_ORDER, Tenant, build_tenant

pytestmark = pytest.mark.db

# Rutas donde el recurso objetivo debe existir para que la comprobación de permiso sea observable.
_counter = {"n": 0}


def _unique_email() -> str:
    _counter["n"] += 1
    return f"matrix-{_counter['n']}@example.com"


def _request(method: str, template: str, t: Tenant) -> tuple[str, Any]:
    path = re.sub(
        r"\{(\w+)\}",
        lambda m: {
            "member_id": t.member_ids["target"],
            "invitation_id": t.invitation_id,
            "token_id": str(uuid.uuid4()),  # los tokens ajenos se comprueban en test_idor
            "org_id": t.org_id,
        }[m.group(1)],
        template,
    )
    body: Any = None
    if (method, template) == ("PATCH", f"{BASE}/members/{{member_id}}"):
        body = {"role": "viewer"}
    elif (method, template) == ("POST", f"{BASE}/invitations"):
        body = {"email": _unique_email(), "role": "viewer"}
    elif (method, template) == ("POST", f"{BASE}/me/api-tokens"):
        body = {"name": "matrix", "scopes": ["media:read"]}
    elif (method, template) == ("PATCH", f"{BASE}/orgs/{{org_id}}"):
        body = {"name": "Matrix Org"}
    return path.removeprefix(BASE), body


def _org_routes() -> list[tuple[tuple[str, str], Policy]]:
    return [(k, p) for k, p in POLICY.items() if p.kind in ("org_header", "org_path")]


@pytest.fixture
async def tenant(api: ApiHarness, users: Users) -> Tenant:
    return await build_tenant(api, users, "m")


async def test_session_role_matrix(api: ApiHarness, tenant: Tenant) -> None:
    results: list[str] = []
    for (method, template), policy in _org_routes():
        # Los roles se recorren de menor a mayor para que las acciones destructivas de los
        # roles con permiso (p. ej. borrar al miembro objetivo) ocurran después de las denegaciones.
        for role in ROLE_ORDER:
            actor = tenant.actor(role)
            path, body = _request(method, template, tenant)
            r = await actor.call(method, path, org=tenant.org_id, json=body)
            denied = (
                policy.permission is not None and policy.permission not in ROLE_PERMISSIONS[role]
            )
            if denied:
                ok = r.status_code == 403
            else:
                ok = r.status_code not in (401, 403) and r.status_code < 500
            results.append(
                f"{'OK ' if ok else 'BAD'} {role:8} {method:6} {template} -> {r.status_code}"
            )
    bad = [line for line in results if line.startswith("BAD")]
    assert not bad, "Matriz rol × endpoint incorrecta:\n" + "\n".join(bad)
    assert len(results) == len(_org_routes()) * len(ROLE_ORDER)


async def test_denials_do_not_change_state(api: ApiHarness, tenant: Tenant) -> None:
    """Un 403 no debe haber ejecutado la acción: el miembro objetivo y la invitación siguen ahí."""
    for role in ("viewer", "reviewer", "editor"):
        actor = tenant.actor(role)
        for method, template in (
            ("PATCH", f"{BASE}/members/{{member_id}}"),
            ("DELETE", f"{BASE}/members/{{member_id}}"),
            ("DELETE", f"{BASE}/invitations/{{invitation_id}}"),
            ("PATCH", f"{BASE}/orgs/{{org_id}}"),
        ):
            path, body = _request(method, template, tenant)
            r = await actor.call(method, path, org=tenant.org_id, json=body)
            assert r.status_code == 403, (role, method, template)
    owner = tenant.owner
    members = {m["id"]: m for m in (await owner.call("GET", "/members", org=tenant.org_id)).json()}
    assert tenant.member_ids["target"] in members
    pending = (await owner.call("GET", "/invitations", org=tenant.org_id)).json()
    assert [i["id"] for i in pending] == [tenant.invitation_id]
    assert (await owner.call("GET", f"/orgs/{tenant.org_id}")).json()["name"] != "Matrix Org"


async def test_api_token_scopes_limit_the_matrix(api: ApiHarness, tenant: Tenant) -> None:
    """Un token del owner con solo media:read no puede usar ningún endpoint con permiso propio."""
    raw = (
        await tenant.owner.call(
            "POST",
            "/me/api-tokens",
            org=tenant.org_id,
            json={"name": "ro", "scopes": ["media:read"]},
        )
    ).json()["token"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as anon:
        for (method, template), policy in _org_routes():
            if policy.permission is None:
                continue
            path, body = _request(method, template, tenant)
            r = await anon.request(
                method, f"{BASE}{path}", json=body, headers={"Authorization": f"Bearer {raw}"}
            )
            if policy.self_service:
                # salir de la organización no exige permiso, pero el objetivo no es el propio
                assert r.status_code in (403, 404), (method, template, r.status_code)
            else:
                assert r.status_code == 403, f"{method} {template}: {r.status_code}"
