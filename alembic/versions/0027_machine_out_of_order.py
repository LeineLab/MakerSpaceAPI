"""add out-of-order status to machines

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.add_column(sa.Column("out_of_order", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("out_of_order_reason", sa.String(255), nullable=True))
        batch.add_column(sa.Column("out_of_order_since", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.drop_column("out_of_order_since")
        batch.drop_column("out_of_order_reason")
        batch.drop_column("out_of_order")
