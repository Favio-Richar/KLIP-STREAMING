"""storage_objects: registro de objetos del almacén (ADR-005) con RLS por organización.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storage_objects",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("backend", sa.Text(), nullable=False, server_default="s3"),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", pg.BYTEA()),
        sa.Column("mime", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="uploading"),
        sa.Column("ref_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orphaned_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_storage_objects__org_id"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL", name="fk_storage_objects__created_by"
        ),
        sa.UniqueConstraint("bucket", "key", name="uq_storage_objects__bucket_key"),
        sa.CheckConstraint("backend in ('s3')", name="ck_storage_objects__backend"),
        sa.CheckConstraint(
            "status in ('uploading','ready','orphan','deleted')", name="ck_storage_objects__status"
        ),
        sa.CheckConstraint("ref_count >= 0", name="ck_storage_objects__ref_count"),
        sa.CheckConstraint(
            "sha256 is null or length(sha256) = 32", name="ck_storage_objects__sha256"
        ),
        # Una clave siempre vive bajo el prefijo de su organización (defensa en profundidad).
        sa.CheckConstraint(
            "key like 'org/' || org_id::text || '/%'", name="ck_storage_objects__key_prefix"
        ),
    )
    op.create_index("ix_storage_objects__org_id_sha256", "storage_objects", ["org_id", "sha256"])
    op.execute(
        "CREATE INDEX ix_storage_objects__orphans ON storage_objects (orphaned_at) "
        "WHERE status = 'orphan'"
    )
    op.execute("SELECT apply_updated_at_trigger('storage_objects')")
    op.execute("SELECT apply_tenant_rls('storage_objects')")


def downgrade() -> None:
    op.drop_table("storage_objects")
