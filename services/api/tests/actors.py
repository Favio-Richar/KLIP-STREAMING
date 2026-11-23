"""Utilidades de pruebas: actores (usuarios con sesión propia), organizaciones y membresías."""

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from harness import ApiHarness

PASSWORD = "correct-horse-battery"
BASE = "/api/v1"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Actor:
    client: httpx.AsyncClient
    email: str
    csrf: str
    user_id: str

    async def call(
        self,
        method: str,
        path: str,
        *,
        org: str | uuid.UUID | None = None,
        json: Any = None,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        h = dict(headers or {})
        if org is not None:
            h["X-Org-ID"] = str(org)
        if token:
            h["Authorization"] = f"Bearer {token}"
        elif method in UNSAFE:
            h["X-CSRF-Token"] = self.csrf
        return await self.client.request(method, f"{BASE}{path}", json=json, headers=h)


@asynccontextmanager
async def _client(api: ApiHarness) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class Users:
    """Fábrica de usuarios verificados y con sesión iniciada, cada uno con su propio cliente."""

    def __init__(self, api: ApiHarness) -> None:
        self.api = api
        self._clients: list[httpx.AsyncClient] = []

    async def make(self, email: str, *, verify: bool = True) -> Actor:
        transport = httpx.ASGITransport(app=self.api.app, raise_app_exceptions=False)
        client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
        self._clients.append(client)
        r = await client.post(
            f"{BASE}/auth/register",
            json={
                "email": email,
                "password": PASSWORD,
                "display_name": email.split("@")[0].title(),
            },
        )
        assert r.status_code == 202, r.text
        if verify:
            mail = next(
                m for m in self.api.emails.sent if m.to == email and "verify-email" in m.body
            )
            token = re.search(r"token=([\w\-]+)", mail.body)
            assert token
            v = await client.post(f"{BASE}/auth/email/verify", json={"token": token.group(1)})
            assert v.status_code == 204
        login = await client.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD})
        assert login.status_code == 200, login.text
        data = login.json()
        return Actor(client, email, data["csrf_token"], data["user"]["id"])

    async def close(self) -> None:
        for c in self._clients:
            await c.aclose()


async def create_org(
    actor: Actor, name: str = "Visionary AI", slug: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if slug:
        body["slug"] = slug
    r = await actor.call("POST", "/orgs", json=body)
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


def invite_token(api: ApiHarness, email: str) -> str:
    mail = [m for m in api.emails.sent if m.to == email and "invitations/accept" in m.body][-1]
    match = re.search(r"token=([\w\-]+)", mail.body)
    assert match
    return match.group(1)


async def add_member(api: ApiHarness, owner: Actor, org_id: str, member: Actor, role: str) -> None:
    r = await owner.call(
        "POST", "/invitations", org=org_id, json={"email": member.email, "role": role}
    )
    assert r.status_code == 201, r.text
    token = invite_token(api, member.email)
    acc = await member.call("POST", f"/invitations/{token}/accept")
    assert acc.status_code == 200, acc.text
