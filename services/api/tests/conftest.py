"""Fixtures compartidas. Las pruebas `db` usan una base temporal migrada con Alembic."""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from klick.core.config import Settings
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "services" / "api" / "alembic.ini"

_DEFAULT_OWNER_URL = "postgresql+asyncpg://klick_owner:klick_dev_password@localhost:5434/klick"


def _owner_url() -> str:
    return (
        os.environ.get("DATABASE_URL_OWNER") or Settings().database_url_owner or _DEFAULT_OWNER_URL
    )


def _with_db(url: str, name: str, user: str | None = None, password: str | None = None) -> str:
    u = make_url(url).set(database=name)
    if user:
        u = u.set(username=user, password=password)
    return u.render_as_string(hide_password=False)


def _alembic(url: str, *args: str) -> None:
    env = {**os.environ, "ALEMBIC_DATABASE_URL": url}
    subprocess.run(  # noqa: S603 - comando fijo, sin entrada externa
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        check=True,
        env=env,
        cwd=REPO_ROOT,
    )


@pytest.fixture(scope="session")
def pg_admin_dsn() -> str:
    return make_url(_owner_url()).set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def _pg_available(pg_admin_dsn: str) -> None:
    async def probe() -> None:
        conn = await asyncpg.connect(pg_admin_dsn, timeout=3)
        await conn.close()

    try:
        asyncio.run(probe())
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("CI"):
            raise
        pytest.skip(f"PostgreSQL no disponible ({type(exc).__name__}); ejecute pnpm infra:up")


@pytest.fixture
async def temp_db(_pg_available: None, pg_admin_dsn: str) -> AsyncIterator[dict[str, str]]:
    """Base temporal con migraciones aplicadas. Devuelve URLs owner/app/system."""
    name = f"klick_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(pg_admin_dsn)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    owner_url = _with_db(_owner_url(), name)
    _alembic(owner_url, "upgrade", "head")
    urls = {
        "name": name,
        "owner": owner_url,
        "app": _with_db(owner_url, name, "klick_app", "klick_app_dev_password"),
        "system": _with_db(owner_url, name, "klick_system", "klick_system_dev_password"),
    }
    try:
        yield urls
    finally:
        admin = await asyncpg.connect(pg_admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
def alembic_cli() -> Iterator[object]:
    yield _alembic
