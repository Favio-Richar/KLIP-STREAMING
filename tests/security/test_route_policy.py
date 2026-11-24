"""Toda ruta está clasificada y exige autenticación y CSRF según su política."""

import re
import uuid
from typing import Any

import httpx
import pytest
from actors import BASE, Users
from harness import ApiHarness
from klick.core.config import Settings
from klick.main import create_app
from sec_policy import POLICY
from sec_routes import classify_routes

pytestmark = pytest.mark.db

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _fill(path: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        return "x" * 43 if name == "token" else str(uuid.uuid4())

    return re.sub(r"\{(\w+)\}", repl, path)


def test_every_route_is_classified_and_matches_its_real_dependencies() -> None:
    app = create_app(Settings(environment="test"))
    actual = classify_routes(app)
    declared = {key: pol.kind for key, pol in POLICY.items()}
    missing = sorted(set(actual) - set(declared))
    assert not missing, f"Rutas sin clasificar en sec_policy.POLICY: {missing}"
    stale = sorted(set(declared) - set(actual))
    assert not stale, f"Entradas de POLICY para rutas inexistentes: {stale}"
    wrong = {k: (declared[k], actual[k]) for k in actual if declared[k] != actual[k]}
    assert not wrong, f"El tipo declarado no coincide con la autenticación real: {wrong}"


async def test_protected_routes_reject_anonymous_callers(api: ApiHarness) -> None:
    for (method, path), policy in POLICY.items():
        if policy.kind == "public":
            continue
        r = await api.client.request(
            method,
            _fill(path),
            json={} if method in UNSAFE else None,
            headers={"X-Org-ID": str(uuid.uuid4())},
        )
        assert r.status_code == 401, f"{method} {path} respondió {r.status_code} sin credenciales"
        assert r.headers["content-type"].startswith("application/problem+json")


async def test_cookie_authenticated_mutations_require_csrf(api: ApiHarness, users: Users) -> None:
    actor = await users.make("csrf@example.com")
    org = (await actor.call("POST", "/orgs", json={"name": "CSRF Org"})).json()
    for (method, path), policy in POLICY.items():
        if policy.kind == "public" or method not in UNSAFE:
            continue
        filled = _fill(path)
        r = await actor.client.request(
            method,
            filled,
            json={},
            headers={"X-Org-ID": org["id"]},  # sin X-CSRF-Token
        )
        assert r.status_code == 403, (
            f"{method} {path}: aceptó una mutación sin CSRF ({r.status_code})"
        )
        assert r.json()["code"] == "csrf_failed"


async def test_bearer_requests_do_not_need_csrf_but_bad_tokens_fail(
    api: ApiHarness, users: Users
) -> None:
    actor = await users.make("bearer@example.com")
    org = (await actor.call("POST", "/orgs", json={"name": "Bearer Org"})).json()
    token = (
        await actor.call(
            "POST", "/me/api-tokens", org=org["id"], json={"name": "t", "scopes": ["media:read"]}
        )
    ).json()["token"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
    ) as anon:
        ok = await anon.get(f"{BASE}/members", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        for header in ("Basic abc", "Bearer", f"Token {token}"):
            bad: Any = await anon.get(f"{BASE}/members", headers={"Authorization": header})
            assert bad.status_code == 401, header
