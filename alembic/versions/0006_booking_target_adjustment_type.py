"""add booking_target_adjustment transaction type

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


_OLD = (
    "purchase", "topup", "transfer_out", "transfer_in",
    "machine_login", "machine_usage",
    "booking_target_topup", "booking_target_payout",
    "admin_adjustment",
)

_NEW = (
    "purchase", "topup", "transfer_out", "transfer_in",
    "machine_login", "machine_usage",
    "booking_target_topup", "booking_target_payout",
    "booking_target_adjustment",
    "admin_adjustment",
)


def _change_enum(from_values, to_values) -> None:
    # SQLite can't ALTER COLUMN TYPE (its Enum is a VARCHAR + CHECK), so recreate
    # the table via batch mode there; MariaDB/MySQL emits a plain MODIFY COLUMN.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("transactions") as batch_op:
            batch_op.alter_column(
                "type",
                existing_type=sa.Enum(*from_values, name="transactiontype"),
                type_=sa.Enum(*to_values, name="transactiontype"),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "transactions",
            "type",
            existing_type=sa.Enum(*from_values, name="transactiontype"),
            type_=sa.Enum(*to_values, name="transactiontype"),
            existing_nullable=False,
        )


def upgrade() -> None:
    _change_enum(_OLD, _NEW)


def downgrade() -> None:
    _change_enum(_NEW, _OLD)
