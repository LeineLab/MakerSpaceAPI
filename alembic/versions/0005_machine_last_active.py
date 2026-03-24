"""add machines.last_active_at

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-24 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("machines", sa.Column("last_active_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("machines", "last_active_at")
