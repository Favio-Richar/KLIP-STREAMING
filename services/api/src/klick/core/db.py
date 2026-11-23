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

_SET_CONFIG_SQL = text("select set_config(:name, :value, true)")

# GUC permitidos. Las políticas RLS de docs/04 §2 y de la migración 0004 solo leen estos.
_ALLOWED_SETTINGS = frozenset(
    {"app.org_id", "app.user_id", "app.invite_token_hash", "app.token_hash"}
)


async def set_context(session: AsyncSession, **values: str | uuid.UUID | None) -> None:
    """Fija variables de contexto locales a la transacción (``org_id``, ``user_id``, ...).

    Las claves se escriben sin el prefijo ``app.``. Un valor ``None`` se ignora.
    """
    for key, value in values.items():
        name = f"app.{key}"
        if name not in _ALLOWED_SETTINGS:
            raise ValueError(f"Contexto no permitido: {name}")
        if value is not None:
            await session.execute(_SET_CONFIG_SQL, {"name": name, "value": str(value)})


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
    async def tenant_session(
        self, org_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> AsyncIterator[AsyncSession]:
        """Transacción con RLS activa para ``org_id`` (y opcionalmente ``user_id``).
        Confirma al salir sin error."""
        async with self._app_factory() as session, session.begin():
            await set_context(session, org_id=org_id, user_id=user_id)
            yield session

    @asynccontextmanager
    async def context_session(
        self, **values: str | uuid.UUID | None
    ) -> AsyncIterator[AsyncSession]:
        """Transacción con contexto arbitrario permitido (p. ej. ``user_id`` o hash de token)."""
        async with self._app_factory() as session, session.begin():
            await set_context(session, **values)
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
