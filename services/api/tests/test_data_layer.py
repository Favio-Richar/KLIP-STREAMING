import uuid
from collections.abc import Callable

import asyncpg
import pytest
from klick.core.config import Settings
from klick.core.db import Database
from klick.core.ids import new_id
from sqlalchemy import text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.db


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def test_uuid7_is_version_7_and_time_ordered() -> None:
    ids = [new_id() for _ in range(50)]
    assert all(i.version == 7 for i in ids)
    assert [str(i) for i in ids] == sorted(str(i) for i in ids)


async def test_migration_creates_roles_functions_and_extensions(temp_db: dict[str, str]) -> None:
    conn = await asyncpg.connect(_dsn(temp_db["owner"]))
    try:
        roles = {
            r["rolname"]: r
            for r in await conn.fetch(
                "select rolname, rolbypassrls, rolsuper from pg_roles "
                "where rolname in ('klick_app','klick_system')"
            )
        }
        assert roles["klick_app"]["rolbypassrls"] is False
        assert roles["klick_app"]["rolsuper"] is False
        assert roles["klick_system"]["rolbypassrls"] is True
        ext = {r["extname"] for r in await conn.fetch("select extname from pg_extension")}
        assert {"vector", "citext", "pg_trgm"} <= ext
        fns = {
            r["proname"]
            for r in await conn.fetch(
                "select proname from pg_proc where proname in "
                "('apply_tenant_rls','apply_reference_rls','set_updated_at',"
                "'apply_updated_at_trigger','uuid_generate_v7')"
            )
        }
        assert len(fns) == 5
        v7 = await conn.fetchval("select uuid_generate_v7()")
        assert v7.version == 7
    finally:
        await conn.close()


async def test_migration_is_reversible(
    temp_db: dict[str, str], alembic_cli: Callable[..., None]
) -> None:
    alembic_cli(temp_db["owner"], "downgrade", "base")
    conn = await asyncpg.connect(_dsn(temp_db["owner"]))
    try:
        count = await conn.fetchval(
            "select count(*) from pg_proc where proname = 'apply_tenant_rls'"
        )
        assert count == 0
    finally:
        await conn.close()
    alembic_cli(temp_db["owner"], "upgrade", "head")


async def test_updated_at_trigger(temp_db: dict[str, str]) -> None:
    conn = await asyncpg.connect(_dsn(temp_db["owner"]))
    try:
        await conn.execute(
            "create table t_upd (id int primary key, v text, updated_at timestamptz)"
        )
        await conn.execute("select apply_updated_at_trigger('t_upd')")
        await conn.execute("insert into t_upd values (1, 'a')")
        assert await conn.fetchval("select updated_at from t_upd where id = 1") is None
        await conn.execute("update t_upd set v = 'b' where id = 1")
        assert await conn.fetchval("select updated_at from t_upd where id = 1") is not None
    finally:
        await conn.close()


async def _make_tenant_table(owner_dsn: str) -> None:
    conn = await asyncpg.connect(owner_dsn)
    try:
        await conn.execute(
            "create table notes (id uuid primary key, org_id uuid not null, body text)"
        )
        await conn.execute("select apply_tenant_rls('notes')")
    finally:
        await conn.close()


async def test_tenant_session_enforces_rls(temp_db: dict[str, str]) -> None:
    await _make_tenant_table(_dsn(temp_db["owner"]))
    db = Database(Settings(database_url=temp_db["app"], database_url_system=temp_db["system"]))
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    try:
        for org, body in ((org_a, "de A"), (org_b, "de B")):
            async with db.tenant_session(org) as s:
                await s.execute(
                    text("insert into notes (id, org_id, body) values (:i, :o, :b)"),
                    {"i": new_id(), "o": org, "b": body},
                )

        # (a) con contexto A solo se ve A
        async with db.tenant_session(org_a) as s:
            rows = (await s.execute(text("select body from notes"))).scalars().all()
            assert rows == ["de A"]

        # (b) no se puede insertar con org_id ajeno
        with pytest.raises(Exception, match="row-level security"):
            async with db.tenant_session(org_a) as s:
                await s.execute(
                    text("insert into notes (id, org_id, body) values (:i, :o, 'x')"),
                    {"i": new_id(), "o": org_b},
                )

        # (b) un UPDATE sobre filas de B no afecta nada
        async with db.tenant_session(org_a) as s:
            res = await s.execute(
                text("update notes set body = 'hack' where org_id = :o returning id"), {"o": org_b}
            )
            assert res.all() == []

        # (c) sin contexto no se ve nada
        async with db.global_session() as s:
            assert (await s.execute(text("select count(*) from notes"))).scalar_one() == 0

        # el contexto no se filtra entre transacciones
        async with db.tenant_session(org_b) as s:
            assert (await s.execute(text("select body from notes"))).scalars().all() == ["de B"]
        async with db.global_session() as s:
            assert (
                await s.execute(text("select current_setting('app.org_id', true)"))
            ).scalar_one() in (
                None,
                "",
            )

        # el rol de sistema ve todo (BYPASSRLS)
        async with db.system_session() as s:
            assert (await s.execute(text("select count(*) from notes"))).scalar_one() == 2
    finally:
        await db.dispose()


async def test_reference_rls_allows_global_rows(temp_db: dict[str, str]) -> None:
    owner = await asyncpg.connect(_dsn(temp_db["owner"]))
    org_a = uuid.uuid4()
    try:
        await owner.execute("create table presets (id uuid primary key, org_id uuid, name text)")
        await owner.execute("select apply_reference_rls('presets')")
        await owner.execute("insert into presets values ($1, null, 'sistema')", new_id())
    finally:
        await owner.close()
    db = Database(Settings(database_url=temp_db["app"]))
    try:
        async with db.tenant_session(org_a) as s:
            assert (await s.execute(text("select name from presets"))).scalars().all() == [
                "sistema"
            ]
            await s.execute(
                text("insert into presets values (:i, :o, 'propio')"), {"i": new_id(), "o": org_a}
            )
        with pytest.raises(Exception, match="row-level security"):
            async with db.tenant_session(org_a) as s:
                await s.execute(
                    text("insert into presets values (:i, null, 'global')"), {"i": new_id()}
                )
        async with db.tenant_session(org_a) as s:
            res = await s.execute(text("delete from presets where org_id is null returning id"))
            assert res.all() == []
    finally:
        await db.dispose()


async def test_database_requires_url() -> None:
    with pytest.raises(RuntimeError):
        Database(Settings(database_url=""))


async def test_ping(temp_db: dict[str, str]) -> None:
    db = Database(Settings(database_url=temp_db["app"]))
    try:
        await db.ping()
    finally:
        await db.dispose()
