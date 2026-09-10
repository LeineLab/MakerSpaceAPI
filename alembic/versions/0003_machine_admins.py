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


def _rename_column(old_name: str, new_name: str) -> None:
    # SQLite doesn't support ALTER TABLE ... ALTER COLUMN outside of batch
    # mode (copy-and-swap); MariaDB/MySQL handles a plain alter_column directly.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("machine_admins") as batch_op:
            batch_op.alter_column(
                old_name,
                new_column_name=new_name,
                existing_type=sa.String(255),
                nullable=False,
            )
    else:
        op.alter_column(
            "machine_admins",
            old_name,
            new_column_name=new_name,
            existing_type=sa.String(255),
            nullable=False,
        )


def upgrade() -> None:
    op.rename_table("machine_admin_groups", "machine_admins")
    _rename_column("oidc_group", "oidc_sub")


def downgrade() -> None:
    _rename_column("oidc_sub", "oidc_group")
    op.rename_table("machine_admins", "machine_admin_groups")
