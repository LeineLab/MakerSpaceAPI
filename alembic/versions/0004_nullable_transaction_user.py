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


def _set_nullable(nullable: bool) -> None:
    # SQLite doesn't support ALTER TABLE ... ALTER COLUMN outside of batch
    # mode (copy-and-swap); MariaDB/MySQL handles a plain alter_column directly.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("transactions") as batch_op:
            batch_op.alter_column(
                "user_id",
                existing_type=sa.BigInteger(),
                nullable=nullable,
            )
    else:
        op.alter_column(
            "transactions",
            "user_id",
            existing_type=sa.BigInteger(),
            nullable=nullable,
        )


def upgrade() -> None:
    _set_nullable(True)


def downgrade() -> None:
    _set_nullable(False)
