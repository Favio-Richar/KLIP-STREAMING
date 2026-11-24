"""Fábricas de filas para la batería RLS. Cada tabla con ``org_id`` DEBE registrarse aquí.

Al crear una tabla de negocio nueva, añade su entrada: ``test_every_org_table_has_a_factory`` falla
si falta, lo que obliga a probar su aislamiento.
"""

import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import asyncpg

Kind = Literal["tenant", "reference", "org_root", "append_only"]


@dataclass(frozen=True)
class Ctx:
    """Datos preexistentes que las fábricas pueden referenciar (usuarios y roles)."""

    user_ids: list[uuid.UUID]
    role_id: uuid.UUID


Factory = Callable[[asyncpg.Connection, uuid.UUID, Ctx], Awaitable[None]]


async def _organizations(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into organizations (id, name, slug) values ($1, 'x', $2)",
        org_id,
        f"s-{secrets.token_hex(6)}",
    )


async def _roles(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into roles (id, org_id, key, name) values (gen_random_uuid(), $1, 'custom', 'x')",
        org_id,
    )


async def _memberships(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into memberships (id, org_id, user_id, role_id) values (gen_random_uuid(), $1, $2, $3)",
        org_id,
        ctx.user_ids[0],
        ctx.role_id,
    )


async def _invitations(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into invitations (id, org_id, email, role_id, token_hash, expires_at) "
        "values (gen_random_uuid(), $1, 'i@example.com', $2, $3, now() + interval '1 day')",
        org_id,
        ctx.role_id,
        secrets.token_hex(16),
    )


async def _api_tokens(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into api_tokens (id, org_id, user_id, name, prefix, token_hash, scopes) "
        "values (gen_random_uuid(), $1, $2, 'x', 'klk_xxxx', $3, array['media:read'])",
        org_id,
        ctx.user_ids[0],
        secrets.token_hex(16),
    )


async def _jobs(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into jobs (id, org_id, queue, kind, correlation_id) "
        "values (gen_random_uuid(), $1, 'default', 'system.ping', gen_random_uuid())",
        org_id,
    )


async def _outbox_events(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into outbox_events (id, org_id, type, aggregate_type) "
        "values (gen_random_uuid(), $1, 'x.y', 'x')",
        org_id,
    )


async def _idempotency_keys(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into idempotency_keys (org_id, scope, key, request_hash, expires_at) "
        "values ($1, 's', $2, 'h', now() + interval '1 day')",
        org_id,
        secrets.token_hex(8),
    )


async def _audit_events(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into audit_events (id, org_id, actor_type, action) "
        "values (gen_random_uuid(), $1, 'system', 'x.y')",
        org_id,
    )


async def _storage_objects(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into storage_objects (id, org_id, bucket, key) "
        "values (gen_random_uuid(), $1::uuid, 'b', 'org/' || $1::uuid::text || '/o/' || $2::text)",
        org_id,
        secrets.token_hex(6),
    )


async def _object(conn: asyncpg.Connection, org_id: uuid.UUID) -> uuid.UUID:
    new_id: uuid.UUID = await conn.fetchval(
        "insert into storage_objects (id, org_id, bucket, key) "
        "values (gen_random_uuid(), $1::uuid, 'b', 'org/' || $1::uuid::text || '/o/' || $2::text) "
        "returning id",
        org_id,
        secrets.token_hex(6),
    )
    return new_id


async def _asset(conn: asyncpg.Connection, org_id: uuid.UUID) -> uuid.UUID:
    new_id: uuid.UUID = await conn.fetchval(
        "insert into media_assets (id, org_id, kind, title, storage_object_id, rights_confirmed_at) "
        "values (gen_random_uuid(), $1, 'video', 'v', $2, now()) returning id",
        org_id,
        await _object(conn, org_id),
    )
    return new_id


async def _projects(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into projects (id, org_id, name) values (gen_random_uuid(), $1, 'p')", org_id
    )


async def _folders(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into folders (id, org_id, name) values (gen_random_uuid(), $1, 'f')", org_id
    )


async def _tags(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into tags (id, org_id, name) values (gen_random_uuid(), $1, 't')", org_id
    )


async def _media_assets(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await _asset(conn, org_id)


async def _media_derivatives(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    await conn.execute(
        "insert into media_derivatives (id, org_id, asset_id, kind, storage_object_id) "
        "values (gen_random_uuid(), $1, $2, 'thumbnail', $3)",
        org_id,
        await _asset(conn, org_id),
        await _object(conn, org_id),
    )


async def _uploads(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    obj = await _object(conn, org_id)
    asset = await conn.fetchval(
        "insert into media_assets (id, org_id, kind, title, storage_object_id, rights_confirmed_at) "
        "values (gen_random_uuid(), $1, 'video', 'v', $2, now()) returning id",
        org_id,
        obj,
    )
    await conn.execute(
        "insert into uploads (id, org_id, asset_id, storage_object_id, expires_at) "
        "values (gen_random_uuid(), $1, $2, $3, now() + interval '1 day')",
        org_id,
        asset,
        obj,
    )


async def _asset_tags(conn: asyncpg.Connection, org_id: uuid.UUID, ctx: Ctx) -> None:
    tag = await conn.fetchval(
        "insert into tags (id, org_id, name) values (gen_random_uuid(), $1, 't') returning id",
        org_id,
    )
    await conn.execute(
        "insert into asset_tags (org_id, asset_id, tag_id) values ($1, $2, $3)",
        org_id,
        await _asset(conn, org_id),
        tag,
    )


# tabla -> (tipo, columna que identifica la organización, fábrica)
FACTORIES: dict[str, tuple[Kind, str, Factory]] = {
    "organizations": ("org_root", "id", _organizations),
    "roles": ("reference", "org_id", _roles),
    "memberships": ("tenant", "org_id", _memberships),
    "invitations": ("tenant", "org_id", _invitations),
    "api_tokens": ("tenant", "org_id", _api_tokens),
    "jobs": ("tenant", "org_id", _jobs),
    "outbox_events": ("tenant", "org_id", _outbox_events),
    "idempotency_keys": ("tenant", "org_id", _idempotency_keys),
    "storage_objects": ("tenant", "org_id", _storage_objects),
    "projects": ("tenant", "org_id", _projects),
    "folders": ("tenant", "org_id", _folders),
    "tags": ("tenant", "org_id", _tags),
    "media_assets": ("tenant", "org_id", _media_assets),
    "media_derivatives": ("tenant", "org_id", _media_derivatives),
    "uploads": ("tenant", "org_id", _uploads),
    "asset_tags": ("tenant", "org_id", _asset_tags),
    # Append-only: la aplicación solo inserta y lee lo suyo; nunca UPDATE ni DELETE.
    "audit_events": ("append_only", "org_id", _audit_events),
}

# Tablas SIN org_id permitidas (CLAUDE.md regla 2: toda tabla de negocio lleva org_id).
# Identidad y catálogos globales; se protegen en la capa de servicio (docs/04 §2).
NO_ORG_TABLES: frozenset[str] = frozenset(
    {
        "alembic_version",
        "users",
        "user_identities",
        "sessions",
        "mfa_factors",
        "mfa_recovery_codes",
        "auth_tokens",
        "permissions",
        "role_permissions",
    }
)
