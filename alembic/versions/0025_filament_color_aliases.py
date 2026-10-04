"""add filament_color_aliases (display names for hex roll colors)

Maps a "#RRGGBB" roll color to a human-readable name for display, optionally
scoped to a brand and/or type. Rolls keep their raw color value; the alias is
resolved at read time, so adding or removing one never touches stock data.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filament_color_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hex", sa.String(7), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=True),
        sa.Column("type_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["brand_id"], ["filament_brands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["type_id"], ["filament_types.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_filament_color_aliases_hex", "filament_color_aliases", ["hex"])


def downgrade() -> None:
    # Drop the table alone, not its index first: InnoDB refuses to drop an
    # index that still covers an FK.
    op.drop_table("filament_color_aliases")
