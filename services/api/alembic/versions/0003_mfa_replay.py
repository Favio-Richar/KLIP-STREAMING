"""MFA: paso TOTP usado por último (anti-replay).

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mfa_factors", sa.Column("last_used_step", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("mfa_factors", "last_used_step")
