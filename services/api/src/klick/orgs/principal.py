"""Principal: quién actúa, en qué organización y con qué permisos (docs/05 §4, docs/09)."""

import uuid
from dataclasses import dataclass
from typing import Literal

from klick.core.errors import ApiError

Origin = Literal["web", "api_token", "mcp"]


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role_key: str
    permissions: frozenset[str]
    origin: Origin
    session_id: uuid.UUID | None = None
    token_id: uuid.UUID | None = None

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, *permissions: str) -> None:
        """Exige todos los permisos indicados; si falta alguno responde 403."""
        missing = [p for p in permissions if p not in self.permissions]
        if missing:
            raise forbidden(missing)


def forbidden(missing: list[str] | None = None) -> ApiError:
    return ApiError(
        403,
        "forbidden",
        "Forbidden",
        "No tienes permiso para realizar esta acción.",
        errors=[{"permission": p} for p in missing] if missing else None,
    )
