"""Identidad: users, user_identities, sessions, mfa_factors, mfa_recovery_codes, auth_tokens.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        *_base_columns(),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text()),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("avatar_storage_object_id", pg.UUID(as_uuid=True)),
        sa.Column("locale", sa.Text(), nullable=False, server_default="es-CL"),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="America/Santiago"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column(
            "is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.UniqueConstraint("email", name="uq_users__email"),
    )

    op.create_table(
        "user_identities",
        *_base_columns(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", pg.CITEXT()),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_user_identities__user_id"
        ),
        sa.UniqueConstraint("provider", "subject", name="uq_user_identities__provider_subject"),
        sa.CheckConstraint("provider in ('google')", name="ck_user_identities__provider"),
    )
    op.create_index("ix_user_identities__user_id", "user_identities", ["user_id"])

    op.create_table(
        "sessions",
        *_base_columns(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("family_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_agent", sa.Text()),
        sa.Column("ip", pg.INET()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("rotated_from", pg.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_sessions__user_id"
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from"], ["sessions.id"], ondelete="SET NULL", name="fk_sessions__rotated_from"
        ),
        sa.UniqueConstraint("refresh_token_hash", name="uq_sessions__refresh_token_hash"),
    )
    op.create_index("ix_sessions__user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions__family_id", "sessions", ["family_id"])

    op.create_table(
        "mfa_factors",
        *_base_columns(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("secret_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_mfa_factors__user_id"
        ),
        sa.CheckConstraint("type in ('totp')", name="ck_mfa_factors__type"),
    )
    op.create_index("ix_mfa_factors__user_id", "mfa_factors", ["user_id"])

    op.create_table(
        "mfa_recovery_codes",
        *_base_columns(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_mfa_recovery_codes__user_id"
        ),
    )
    op.create_index("ix_mfa_recovery_codes__user_id", "mfa_recovery_codes", ["user_id"])

    op.create_table(
        "auth_tokens",
        *_base_columns(),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_auth_tokens__user_id"
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens__token_hash"),
        sa.CheckConstraint(
            "purpose in ('verify_email','reset_password')", name="ck_auth_tokens__purpose"
        ),
    )
    op.create_index("ix_auth_tokens__user_id", "auth_tokens", ["user_id"])

    for table in (
        "users",
        "user_identities",
        "sessions",
        "mfa_factors",
        "mfa_recovery_codes",
        "auth_tokens",
    ):
        op.execute(f"SELECT apply_updated_at_trigger('{table}')")


def downgrade() -> None:
    for table in (
        "auth_tokens",
        "mfa_recovery_codes",
        "mfa_factors",
        "sessions",
        "user_identities",
        "users",
    ):
        op.drop_table(table)
