"""Migración de documentos de edición entre versiones del esquema (docs/07 §2).

Un cambio mayor de ``schema_version`` trae una función de migración registrada aquí y probada con
fixtures. ``migrate_edit_project`` aplica la cadena necesaria hasta la versión pedida; no valida:
el resultado debe pasar por ``parse_edit_project``. Nunca modifica el documento recibido.
"""

import copy
from collections.abc import Callable, Mapping
from typing import Any

CURRENT_VERSION = "1.0"

Migration = Callable[[dict[str, Any]], dict[str, Any]]
# versión de origen -> (versión de destino, función). Vacío hasta que exista una versión 2.
MIGRATIONS: dict[str, tuple[str, Migration]] = {}


class MigrationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _key(version: str) -> tuple[int, int]:
    major, _, minor = version.partition(".")
    if not major.isdigit() or not minor.isdigit():
        raise MigrationError("invalid_version", f"Versión de esquema inválida: {version!r}")
    return int(major), int(minor)


def migrate_edit_project(
    doc: dict[str, Any],
    *,
    to: str = CURRENT_VERSION,
    registry: Mapping[str, tuple[str, Migration]] | None = None,
) -> dict[str, Any]:
    """Devuelve una copia del documento en la versión ``to`` aplicando las migraciones en cadena."""
    steps = MIGRATIONS if registry is None else registry
    version = doc.get("schema_version") if isinstance(doc, dict) else None
    if not isinstance(version, str):
        raise MigrationError("missing_version", "El documento no declara schema_version.")
    if _key(version) > _key(to):
        raise MigrationError(
            "document_from_the_future",
            f"El documento es de la versión {version}, más nueva que {to}.",
        )
    out = copy.deepcopy(doc)
    visited: set[str] = set()
    while version != to:
        if version in visited:
            raise MigrationError("migration_cycle", f"Ciclo de migraciones en {version}.")
        visited.add(version)
        step = steps.get(version)
        if step is None:
            raise MigrationError(
                "no_migration_path", f"No hay migración desde la versión {version}."
            )
        target, fn = step
        out = fn(copy.deepcopy(out))
        out["schema_version"] = target
        version = target
    return out
