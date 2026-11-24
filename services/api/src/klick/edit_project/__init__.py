"""Documento de edición (``EditProject``): modelos generados, validación y migraciones."""

from klick.edit_project.migrate import CURRENT_VERSION, MigrationError, migrate_edit_project
from klick.edit_project.models import EditProject
from klick.edit_project.validate import EditProjectInvalid, Issue, parse_edit_project

__all__ = [
    "CURRENT_VERSION",
    "EditProject",
    "EditProjectInvalid",
    "Issue",
    "MigrationError",
    "migrate_edit_project",
    "parse_edit_project",
]
