"""Identificadores UUIDv7 generados en la aplicación (ADR-004)."""

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    """UUIDv7 ordenable por tiempo, devuelto como uuid.UUID estándar."""
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
