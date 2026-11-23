"""Acceso a PostgreSQL: motores asíncronos y contexto de organización por transacción.

Regla 1 de CLAUDE.md: solo la capa de servicios abre sesiones. Los routers no.
Regla de tenant (docs/04 §2): cada transacción fija ``app.org_id`` antes de cualquier consulta.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from klick.core.config import Settings

_SET_ORG_SQL = text("select set_config('app.org_id', :org_id, true)")


class Database:
    """Agrupa el motor de la aplicación (sin BYPASSRLS) y el del sistema (BYPASSRLS)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL no está configurada")
        self._app_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        self._app_factory = async_sessionmaker(self._app_engine, expire_on_commit=False)
        self._system_engine: AsyncEngine | None = None
        self._system_factory: async_sessionmaker[AsyncSession] | None = None
        if settings.database_url_system:
            self._system_engine = create_async_engine(
                settings.database_url_system, pool_pre_ping=True
            )
            self._system_factory = async_sessionmaker(self._system_engine, expire_on_commit=False)

    @asynccontextmanager
    async def tenant_session(self, org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        """Transacción con RLS activa para ``org_id``. Confirma al salir sin error."""
        async with self._app_factory() as session, session.begin():
            await session.execute(_SET_ORG_SQL, {"org_id": str(org_id)})
            yield session

    @asynccontextmanager
    async def global_session(self) -> AsyncIterator[AsyncSession]:
        """Transacción sin contexto de organización, solo para tablas sin ``org_id``
        (usuarios, sesiones, permisos). Con RLS activa no ve filas de tablas de negocio."""
        async with self._app_factory() as session, session.begin():
            yield session

    @asynccontextmanager
    async def system_session(self) -> AsyncIterator[AsyncSession]:
        """Rol ``klick_system`` (BYPASSRLS). Solo scheduler, reconciliador y retención."""
        if self._system_factory is None:
            raise RuntimeError("DATABASE_URL_SYSTEM no está configurada")
        async with self._system_factory() as session, session.begin():
            yield session

    async def ping(self) -> None:
        async with self._app_engine.connect() as conn:
            await conn.execute(text("select 1"))

    async def dispose(self) -> None:
        await self._app_engine.dispose()
        if self._system_engine is not None:
            await self._system_engine.dispose()
