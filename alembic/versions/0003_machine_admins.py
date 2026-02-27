"""rename machine_admin_groups to machine_admins, oidc_group -> oidc_sub

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("machine_admin_groups", "machine_admins")
    op.alter_column(
        "machine_admins",
        "oidc_group",
        new_column_name="oidc_sub",
        existing_type=sa.String(255),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "machine_admins",
        "oidc_sub",
        new_column_name="oidc_group",
        existing_type=sa.String(255),
        nullable=False,
    )
    op.rename_table("machine_admins", "machine_admin_groups")
