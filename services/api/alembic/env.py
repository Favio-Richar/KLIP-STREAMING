"""Entorno de Alembic. Las migraciones corren con el rol dueño (klick_owner)."""

import asyncio
import os

from alembic import context
from klick.core.config import get_settings
from klick.core.models import Base
from klick.identity import models as _identity_models  # noqa: F401
from klick.orgs import models as _orgs_models  # noqa: F401
from klick.storage import models as _storage_models  # noqa: F401
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # ALEMBIC_DATABASE_URL permite a las pruebas apuntar a una base temporal.
    url = os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url_owner
    if not url:
        raise RuntimeError("Defina DATABASE_URL_OWNER (o ALEMBIC_DATABASE_URL) para migrar")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
