"""inventory: locations, items, expiry batches

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-22 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_locations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("inventory_locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(20), nullable=True),
        sa.Column("target_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("track_expiry", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.String(255), nullable=True),
    )
    op.create_index("ix_inventory_items_location_id", "inventory_items", ["location_id"])
    op.create_table(
        "inventory_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
    )
    op.create_index("ix_inventory_batches_item_id", "inventory_batches", ["item_id"])


def downgrade() -> None:
    # Dropping the tables removes their indexes with them; a standalone
    # DROP INDEX on an FK-covering index fails on MariaDB/InnoDB.
    op.drop_table("inventory_batches")
    op.drop_table("inventory_items")
    op.drop_table("inventory_locations")
