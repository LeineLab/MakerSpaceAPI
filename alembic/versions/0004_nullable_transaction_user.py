"""allow transactions.user_id to be null (anonymous donations)

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-28 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "transactions",
        "user_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "transactions",
        "user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
