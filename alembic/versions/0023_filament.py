"""add filament roll tracking (filament_brands, filament_types, filament_rolls)

Inventory-count tracking for 3D-printer filament — how many rolls of a given
brand/type/weight/color are currently in stock, not gram-level consumption
tracking. Brand and type are reference tables (referenced by id from a roll,
same "managed list, referenced elsewhere" pattern as product_categories) so
they can be maintained and reused without retyping free text every time.

Each physical spool is its own row (filament_rolls), mirroring the
rented_at/returned_at pattern already used by `rentals` — added_at/removed_at
instead, NULL removed_at meaning still in stock — rather than a single
per-spec counter, so an individual roll can later be identified by its own
vendor-encoded serial number when the manufacturer's NFC tag carries one
(Bambu Lab, Creality, Open3DTag). OpenSpool tags carry no such serial, so
vendor_serial is nullable; those rolls are only ever matched by spec.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filament_brands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_filament_brands_name"),
    )

    op.create_table(
        "filament_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_filament_types_name"),
    )

    op.create_table(
        "filament_rolls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=False),
        sa.Column("type_id", sa.Integer(), nullable=False),
        sa.Column("weight_grams", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(50), nullable=False),
        sa.Column("vendor_serial", sa.String(64), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("added_by", sa.String(255), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("removed_by", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["brand_id"], ["filament_brands.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["type_id"], ["filament_types.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_filament_rolls_spec", "filament_rolls",
        ["brand_id", "type_id", "weight_grams", "color"],
    )
    op.create_index("ix_filament_rolls_vendor_serial", "filament_rolls", ["vendor_serial"])


def downgrade() -> None:
    # Drop the whole table rather than its indexes individually — dropping a
    # table removes its indexes/constraints atomically on every backend,
    # sidestepping the InnoDB "index still covers an FK" drop-order error
    # documented for migrations 0007/0008/0013/0015 (see CLAUDE.md #43).
    op.drop_table("filament_rolls")
    op.drop_table("filament_types")
    op.drop_table("filament_brands")
