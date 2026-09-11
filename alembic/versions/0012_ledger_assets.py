"""add ledger_assets table (Anlagevermögen / AfA)

A ledger_assets row links an already-booked, category-side ledger_entry_line
(the real cash outflow at purchase) to a depreciation schedule (useful_life_years,
a target "Abschreibungen" category). Once linked, that line's full amount is
excluded from the EÜR in its booking year (see Key Design Decision #35) and
replaced, at report-computation time, by the linear/monatsgenau AfA amount for
each year of the asset's useful life — no separate yearly booking rows, same
read-time-aggregation approach as the Kassen bridge (#34).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ledger_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("entry_line_id", sa.Integer(), nullable=False),
        sa.Column("acquisition_date", sa.Date(), nullable=False),
        sa.Column("acquisition_cost", sa.Numeric(10, 2), nullable=False),
        sa.Column("useful_life_years", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("disposed_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["entry_line_id"], ["ledger_entry_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["ledger_categories.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("entry_line_id", name="uq_ledger_assets_entry_line_id"),
    )


def downgrade() -> None:
    op.drop_table("ledger_assets")
