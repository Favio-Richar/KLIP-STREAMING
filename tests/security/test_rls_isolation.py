"""Batería genérica de aislamiento RLS (docs/04 §2, ADR-023). Bloquea CI.

Para cada tabla con ``org_id``: (a) con contexto A no se leen filas de B, (b) no se puede insertar,
actualizar ni borrar con ``org_id`` de B, (c) sin contexto no se ve nada de negocio.
Si aparece una tabla con ``org_id`` sin política o sin fábrica, la batería falla.
"""

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sec_factories import FACTORIES, NO_ORG_TABLES, Ctx
from sec_scenario import dsn
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


async def _owner(urls: dict[str, str]) -> asyncpg.Connection:
    return await asyncpg.connect(dsn(urls["owner"]))


async def _app(urls: dict[str, str]) -> asyncpg.Connection:
    return await asyncpg.connect(dsn(urls["app"]))


async def _all_tables(conn: asyncpg.Connection) -> dict[str, set[str]]:
    # Las particiones heredan la política del padre y no se consultan directamente.
    rows = await conn.fetch(
        "select c.table_name, c.column_name from information_schema.columns c "
        "join pg_class k on k.relname = c.table_name and k.relnamespace = 'public'::regnamespace "
        "where c.table_schema = 'public' and not k.relispartition"
    )
    tables: dict[str, set[str]] = {}
    for r in rows:
        tables.setdefault(r["table_name"], set()).add(r["column_name"])
    return tables


@pytest.fixture
async def seeded(temp_db: dict[str, str]) -> AsyncIterator[tuple[dict[str, str], Ctx]]:
    conn = await _owner(temp_db)
    try:
        users = []
        for i in range(2):
            uid = uuid.uuid4()
            await conn.execute(
                "insert into users (id, email, display_name) values ($1, $2, 'u')",
                uid,
                f"u{i}@example.com",
            )
            users.append(uid)
        role_id = await conn.fetchval(
            "select id from roles where key = 'viewer' and org_id is null"
        )
        yield temp_db, Ctx(user_ids=users, role_id=role_id)
    finally:
        await conn.close()


# ----------------------------------------------------------------------------- estructura


async def test_every_table_with_org_id_has_rls_forced_and_a_policy(
    temp_db: dict[str, str],
) -> None:
    conn = await _owner(temp_db)
    try:
        tables = await _all_tables(conn)
        scoped = sorted(t for t, cols in tables.items() if "org_id" in cols) + ["organizations"]
        for table in scoped:
            flags = await conn.fetchrow(
                "select relrowsecurity, relforcerowsecurity from pg_class "
                "where relname = $1 and relnamespace = 'public'::regnamespace",
                table,
            )
            assert flags and flags["relrowsecurity"], f"{table}: RLS no habilitada"
            assert flags["relforcerowsecurity"], f"{table}: RLS no forzada para el dueño"
            policies = await conn.fetch(
                "select policyname, coalesce(qual, '') || coalesce(with_check, '') as expr "
                "from pg_policies where tablename = $1",
                table,
            )
            assert policies, f"{table}: sin políticas"
            assert any(
                "app.org_id" in p["expr"] for p in policies
            ), f"{table}: ninguna política usa app.org_id"
    finally:
        await conn.close()


async def test_every_org_table_has_a_factory(temp_db: dict[str, str]) -> None:
    conn = await _owner(temp_db)
    try:
        tables = await _all_tables(conn)
    finally:
        await conn.close()
    scoped = {t for t, cols in tables.items() if "org_id" in cols} | {"organizations"}
    missing = scoped - set(FACTORIES)
    assert not missing, f"Registra una fábrica en sec_factories.FACTORIES para: {sorted(missing)}"
    stale = set(FACTORIES) - scoped
    assert not stale, f"Fábricas de tablas inexistentes: {sorted(stale)}"


async def test_tables_without_org_id_are_explicitly_allowed(temp_db: dict[str, str]) -> None:
    conn = await _owner(temp_db)
    try:
        tables = await _all_tables(conn)
    finally:
        await conn.close()
    unscoped = {t for t, cols in tables.items() if "org_id" not in cols} - {"organizations"}
    unknown = unscoped - NO_ORG_TABLES
    assert (
        not unknown
    ), f"Tablas sin org_id que no están en NO_ORG_TABLES (regla 2 de CLAUDE.md): {sorted(unknown)}"


async def test_app_role_has_no_bypass(temp_db: dict[str, str]) -> None:
    conn = await _owner(temp_db)
    try:
        row = await conn.fetchrow(
            "select rolsuper, rolbypassrls from pg_roles where rolname = 'klick_app'"
        )
    finally:
        await conn.close()
    assert row and not row["rolsuper"] and not row["rolbypassrls"]


# ------------------------------------------------------------------------ comportamiento


@pytest.mark.parametrize("table", sorted(FACTORIES))
async def test_isolation_between_two_organizations(
    seeded: tuple[dict[str, str], Ctx], table: str
) -> None:
    urls, ctx = seeded
    kind, key, make = FACTORIES[table]
    org_a, org_b = uuid.uuid4(), uuid.uuid4()

    owner = await _owner(urls)
    try:
        if table != "organizations":
            for org in (org_a, org_b):  # las organizaciones deben existir por las FK
                await FACTORIES["organizations"][2](owner, org, ctx)
        for org in (org_a, org_b):
            await make(owner, org, ctx)
    finally:
        await owner.close()

    app = await _app(urls)
    try:

        async def use(org: uuid.UUID | None) -> None:
            await app.execute("select set_config('app.org_id', $1, false)", str(org) if org else "")

        # (a) con contexto A solo se ven filas de A (o de referencia con org nula)
        await use(org_a)
        visible = {r[0] for r in await app.fetch(f"select {key} from {table}")}
        allowed = {org_a, None} if kind == "reference" else {org_a}
        assert (
            visible <= allowed
        ), f"{table}: con contexto A se ven filas ajenas: {visible - allowed}"
        assert org_a in visible, f"{table}: con contexto A no se ve la fila propia"
        assert not await app.fetch(f"select 1 from {table} where {key} = $1", org_b)

        # (b) no se puede modificar ni borrar lo de B
        if kind == "append_only":  # ni siquiera lo propio: no hay privilegio de UPDATE/DELETE
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.fetch(f"update {table} set {key} = {key} where {key} = $1", org_b)
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.fetch(f"delete from {table} where {key} = $1", org_a)
        else:
            assert not await app.fetch(
                f"update {table} set {key} = {key} where {key} = $1 returning 1", org_b
            )
            assert not await app.fetch(f"delete from {table} where {key} = $1 returning 1", org_b)
        # ... ni insertar en B estando en A
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await make(app, org_b, ctx)
        # ... ni mover una fila propia hacia B
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute(f"update {table} set {key} = $1 where {key} = $2", org_b, org_a)

        # (c) sin contexto o con contexto vacío no se ve nada de negocio
        for empty in (None,):
            await use(empty)
            rows = {r[0] for r in await app.fetch(f"select {key} from {table}")}
            assert rows <= (
                {None} if kind == "reference" else set()
            ), f"{table}: visible sin contexto"
        # y lo de A sigue intacto para su propietario
        await use(org_a)
        assert org_a in {r[0] for r in await app.fetch(f"select {key} from {table}")}
    finally:
        await app.close()


async def test_make_url_roundtrip_sanity() -> None:
    """Evita que un cambio en la construcción de DSN deje las pruebas apuntando al rol equivocado."""
    url = make_url("postgresql+asyncpg://klick_app:p@localhost:5434/x")
    assert dsn(url.render_as_string(hide_password=False)).startswith("postgresql://klick_app:")
