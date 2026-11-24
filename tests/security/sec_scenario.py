"""Escenario multi-organización compartido por las pruebas de IDOR y de la matriz de roles."""

import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from actors import Actor, Users, add_member, create_org
from harness import ApiHarness
from media_helpers import make_asset
from sqlalchemy.engine import make_url

ROLE_ORDER = ("viewer", "reviewer", "editor", "admin", "owner")


def dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def sql(api: ApiHarness, query: str, *args: object) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn(api.db_urls["owner"]))
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


@dataclass
class Tenant:
    org_id: str
    owner: Actor
    members: dict[str, Actor] = field(default_factory=dict)  # rol -> actor
    member_ids: dict[str, str] = field(default_factory=dict)  # rol -> id de membresía
    invitation_id: str = ""
    token_id: str = ""
    token_raw: str = ""
    job_id: str = ""
    project_id: str = ""
    folder_id: str = ""
    tag_id: str = ""
    asset_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def actor(self, role: str) -> Actor:
        return self.owner if role == "owner" else self.members[role]


async def build_tenant(api: ApiHarness, users: Users, tag: str) -> Tenant:
    """Organización con un usuario por rol, una invitación pendiente y un token de API."""
    owner = await users.make(f"owner-{tag}@example.com")
    org = await create_org(owner, f"Org {tag}")
    tenant = Tenant(org_id=org["id"], owner=owner)
    for role in ROLE_ORDER[:-1]:
        actor = await users.make(f"{role}-{tag}@example.com")
        await add_member(api, owner, org["id"], actor, role)
        tenant.members[role] = actor
    target = await users.make(f"target-{tag}@example.com")
    await add_member(api, owner, org["id"], target, "viewer")
    tenant.members["target"] = target
    listing = (await owner.call("GET", "/members", org=org["id"])).json()
    by_email = {m["email"]: m["id"] for m in listing}
    tenant.member_ids = {r: by_email[tenant.actor(r).email] for r in (*ROLE_ORDER, "target")}
    inv = await owner.call(
        "POST",
        "/invitations",
        org=org["id"],
        json={"email": f"pending-{tag}@example.com", "role": "viewer"},
    )
    tenant.invitation_id = inv.json()["id"]
    tok = await owner.call(
        "POST",
        "/me/api-tokens",
        org=org["id"],
        json={"name": f"tok-{tag}", "scopes": ["media:read"]},
    )
    tenant.token_id, tenant.token_raw = tok.json()["id"], tok.json()["token"]
    job = await owner.call(
        "POST",
        "/jobs",
        org=org["id"],
        json={"kind": "system.ping", "payload": {"steps": 1}},
    )
    tenant.job_id = job.json()["job_id"]
    # recursos de la biblioteca de medios (F1-01)
    project = await owner.call("POST", "/projects", org=org["id"], json={"name": f"Proyecto {tag}"})
    tenant.project_id = project.json()["id"]
    folder = await owner.call(
        "POST", "/folders", org=org["id"], json={"name": "Carpeta", "project_id": tenant.project_id}
    )
    tenant.folder_id = folder.json()["id"]
    label = await owner.call("POST", "/tags", org=org["id"], json={"name": f"etiqueta-{tag}"})
    tenant.tag_id = label.json()["id"]
    tenant.asset_id = str(
        await make_asset(
            api,
            org["id"],
            owner.user_id,
            title=f"Video {tag}",
            status="ready",
            project_id=uuid.UUID(tenant.project_id),
            folder_id=uuid.UUID(tenant.folder_id),
        )
    )
    return tenant


def random_uuid() -> str:
    return str(uuid.uuid4())


def media_path_values(t: "Tenant") -> dict[str, str]:
    """Identificadores de los recursos de medios de una organización, por nombre de parámetro."""
    return {
        "project_id": t.project_id,
        "folder_id": t.folder_id,
        "tag_id": t.tag_id,
        "asset_id": t.asset_id,
    }


# Cuerpos válidos para las escrituras de medios (clave: método y plantilla relativa a /api/v1).
MEDIA_BODIES: dict[tuple[str, str], object] = {
    ("POST", "/projects"): {"name": "pwn"},
    ("PATCH", "/projects/{project_id}"): {"name": "pwn"},
    ("POST", "/folders"): {"name": "pwn"},
    ("PATCH", "/folders/{folder_id}"): {"name": "pwn"},
    ("POST", "/tags"): {"name": "pwn"},
    ("PATCH", "/assets/{asset_id}"): {"title": "pwn"},
}
