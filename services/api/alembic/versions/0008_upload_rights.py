"""Confirmación de derechos de contenido en assets subidos (docs/09 §contenido de terceros).

Todo asset con `source_type = 'upload'` guarda cuándo y quién confirmó "tengo derecho a usar este
contenido". Es una restricción de la base de datos: ningún camino (API, agente, MCP, script) puede
crear un asset subido sin esa confirmación.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE media_assets ADD COLUMN rights_confirmed_at timestamptz")
    op.execute(
        "ALTER TABLE media_assets ADD COLUMN rights_confirmed_by uuid "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    # Los assets previos (solo datos de desarrollo) se consideran confirmados al crearlos.
    op.execute(
        "UPDATE media_assets SET rights_confirmed_at = created_at, rights_confirmed_by = created_by "
        "WHERE source_type = 'upload'"
    )
    op.execute(
        "ALTER TABLE media_assets ADD CONSTRAINT ck_media_assets__upload_rights "
        "CHECK (source_type <> 'upload' OR rights_confirmed_at IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE media_assets DROP CONSTRAINT ck_media_assets__upload_rights")
    op.execute("ALTER TABLE media_assets DROP COLUMN rights_confirmed_by")
    op.execute("ALTER TABLE media_assets DROP COLUMN rights_confirmed_at")
