"""add user_card_aliases table

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-30 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_card_aliases",
        sa.Column("alias_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("alias_id"),
    )
    op.create_index("ix_user_card_aliases_user_id", "user_card_aliases", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_card_aliases_user_id", table_name="user_card_aliases")
    op.drop_table("user_card_aliases")
