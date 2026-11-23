"""Organizaciones, permisos, roles, membresías, invitaciones y tokens de API (con RLS).

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG = "nullif(current_setting('app.org_id', true), '')::uuid"
USER = "nullif(current_setting('app.user_id', true), '')::uuid"
INVITE = "nullif(current_setting('app.invite_token_hash', true), '')"
TOKEN = "nullif(current_setting('app.token_hash', true), '')"


def _cols() -> list[sa.Column[object]]:
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "organizations",
        *_cols(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("plan_id", pg.UUID(as_uuid=True)),
        sa.Column("settings", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_organizations__created_by"),
        sa.UniqueConstraint("slug", name="uq_organizations__slug"),
    )

    op.create_table(
        "permissions",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
    )

    op.create_table(
        "roles",
        *_cols(),
        sa.Column("org_id", pg.UUID(as_uuid=True)),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_roles__org_id"
        ),
        sa.UniqueConstraint(
            "org_id", "key", name="uq_roles__org_id_key", postgresql_nulls_not_distinct=True
        ),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_key", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_key", name="pk_role_permissions"),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], ondelete="CASCADE", name="fk_role_permissions__role_id"
        ),
        sa.ForeignKeyConstraint(
            ["permission_key"],
            ["permissions.key"],
            ondelete="CASCADE",
            name="fk_role_permissions__permission_key",
        ),
    )

    op.create_table(
        "memberships",
        *_cols(),
        sa.Column("org_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_memberships__org_id"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_memberships__user_id"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_memberships__role_id"),
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships__org_id_user_id"),
        sa.CheckConstraint(
            "status in ('invited','active','suspended')", name="ck_memberships__status"
        ),
    )
    op.create_index("ix_memberships__user_id", "memberships", ["user_id"])

    op.create_table(
        "invitations",
        *_cols(),
        sa.Column("org_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("role_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("invited_by", pg.UUID(as_uuid=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_invitations__org_id"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_invitations__role_id"),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], ondelete="SET NULL", name="fk_invitations__invited_by"
        ),
        sa.UniqueConstraint("token_hash", name="uq_invitations__token_hash"),
    )
    op.create_index("ix_invitations__org_id", "invitations", ["org_id"])

    op.create_table(
        "api_tokens",
        *_cols(),
        sa.Column("org_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("scopes", pg.ARRAY(sa.Text()), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_api_tokens__org_id"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_api_tokens__user_id"
        ),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens__token_hash"),
    )
    op.create_index("ix_api_tokens__org_id", "api_tokens", ["org_id"])

    for table in ("organizations", "roles", "memberships", "invitations", "api_tokens"):
        op.execute(f"SELECT apply_updated_at_trigger('{table}')")

    # Las tablas de catálogo son de solo lectura para la aplicación (las siembra el sistema).
    op.execute("REVOKE INSERT, UPDATE, DELETE ON permissions, role_permissions FROM klick_app")

    # --- RLS --------------------------------------------------------------------------------
    for table in ("organizations", "memberships", "invitations", "api_tokens"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # organizations: se ve la organización del contexto y las de las que el usuario es miembro.
    op.execute(
        f"""CREATE POLICY org_select ON organizations FOR SELECT USING (
              id = {ORG} OR id IN (
                SELECT org_id FROM memberships WHERE user_id = {USER} AND status = 'active'))"""
    )
    for cmd, clause in (
        ("INSERT", f"WITH CHECK (id = {ORG})"),
        ("UPDATE", f"USING (id = {ORG}) WITH CHECK (id = {ORG})"),
        ("DELETE", f"USING (id = {ORG})"),
    ):
        op.execute(f"CREATE POLICY org_{cmd.lower()} ON organizations FOR {cmd} {clause}")

    # memberships: las de la organización del contexto o las propias del usuario (lectura).
    op.execute(
        f"CREATE POLICY membership_select ON memberships FOR SELECT "
        f"USING (org_id = {ORG} OR user_id = {USER})"
    )
    for cmd, clause in (
        ("INSERT", f"WITH CHECK (org_id = {ORG})"),
        ("UPDATE", f"USING (org_id = {ORG}) WITH CHECK (org_id = {ORG})"),
        ("DELETE", f"USING (org_id = {ORG})"),
    ):
        op.execute(f"CREATE POLICY membership_{cmd.lower()} ON memberships FOR {cmd} {clause}")

    # invitations y api_tokens: además se pueden leer por el hash del token presentado.
    op.execute(
        f"CREATE POLICY invitation_select ON invitations FOR SELECT "
        f"USING (org_id = {ORG} OR token_hash = {INVITE})"
    )
    op.execute(
        f"CREATE POLICY api_token_select ON api_tokens FOR SELECT "
        f"USING (org_id = {ORG} OR token_hash = {TOKEN})"
    )
    for table, prefix in (("invitations", "invitation"), ("api_tokens", "api_token")):
        for cmd, clause in (
            ("INSERT", f"WITH CHECK (org_id = {ORG})"),
            ("UPDATE", f"USING (org_id = {ORG}) WITH CHECK (org_id = {ORG})"),
            ("DELETE", f"USING (org_id = {ORG})"),
        ):
            op.execute(f"CREATE POLICY {prefix}_{cmd.lower()} ON {table} FOR {cmd} {clause}")

    op.execute("SELECT apply_reference_rls('roles')")


def downgrade() -> None:
    # La política de organizations consulta memberships: se suelta antes de borrar tablas.
    op.execute("DROP POLICY IF EXISTS org_select ON organizations")
    for table in (
        "api_tokens",
        "invitations",
        "memberships",
        "role_permissions",
        "roles",
        "permissions",
        "organizations",
    ):
        op.drop_table(table)
