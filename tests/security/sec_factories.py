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

Kind = Literal["tenant", "reference", "org_root"]


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


# tabla -> (tipo, columna que identifica la organización, fábrica)
FACTORIES: dict[str, tuple[Kind, str, Factory]] = {
    "organizations": ("org_root", "id", _organizations),
    "roles": ("reference", "org_id", _roles),
    "memberships": ("tenant", "org_id", _memberships),
    "invitations": ("tenant", "org_id", _invitations),
    "api_tokens": ("tenant", "org_id", _api_tokens),
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
