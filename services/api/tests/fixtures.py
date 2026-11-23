"""Fixtures compartidas. Las pruebas `db` usan una base temporal migrada con Alembic."""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from actors import Users
from harness import ApiHarness
from klick.core.config import Settings
from klick.core.db import Database
from klick.core.email import MemoryEmailSender
from klick.core.seed import seed_permissions_and_roles
from klick.main import create_app
from pydantic import SecretStr
from redis.asyncio import Redis
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
def pg_available(pg_admin_dsn: str) -> None:
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
async def temp_db(pg_available: None, pg_admin_dsn: str) -> AsyncIterator[dict[str, str]]:
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
    seed_db = Database(Settings(database_url=urls["system"]))
    try:
        async with seed_db.global_session() as session:
            await seed_permissions_and_roles(session)
    finally:
        await seed_db.dispose()
    try:
        yield urls
    finally:
        admin = await asyncpg.connect(pg_admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
def alembic_cli() -> Iterator[object]:
    yield _alembic


def _test_redis_url() -> str:
    base = os.environ.get("REDIS_URL") or Settings().redis_url
    return base.rsplit("/", 1)[0] + "/15"  # base 15: aislada de uso manual


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(_test_redis_url(), decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Redis no disponible ({type(exc).__name__}); ejecute pnpm infra:up")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def api(temp_db: dict[str, str], redis_client: Redis) -> AsyncIterator[ApiHarness]:
    settings = Settings(
        environment="test",
        database_url=temp_db["app"],
        database_url_system=temp_db["system"],
        redis_url=_test_redis_url(),
        app_secret_key=SecretStr("test-secret-key-for-hmac-and-jwt-0123456789"),
        public_web_url="http://web.test",
        rate_limit_register_ip=10_000,
        rate_limit_login_ip=10_000,
    )
    emails = MemoryEmailSender()
    app = create_app(settings, email_sender=emails)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield ApiHarness(app, client, emails, temp_db, redis_client, settings)


@pytest.fixture
async def users(api: ApiHarness) -> AsyncIterator[Users]:
    u = Users(api)
    yield u
    await u.close()
