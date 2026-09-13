"""split ledger_assets into ledger_assets + ledger_asset_components, with
per-component acquisition/disposal dates

The 1:1 `ledger_assets.entry_line_id` (UNIQUE) couldn't represent two real
cases raised by the user: a single booked line only partially qualifying as
a capital asset (the rest should stay a normal one-off expense), and several
separately-booked purchases that only have functional value together (e.g.
a computer's individually-bought parts) — German tax law requires treating
those as one Wirtschaftsgut once their combined cost crosses the GWG
threshold, which a strict one-line-per-asset link can't model at all. See
Key Design Decision #49.

`ledger_asset_components` replaces the old `entry_line_id`/`acquisition_cost`
columns with a proper many-to-many: one row per (asset, entry line, amount)
contribution, `amount` allowed to be less than the line's full amount.
`ledger_assets.acquisition_cost` becomes the sum of its components (see the
model's `acquisition_cost` property) instead of a stored column.

`acquisition_date`/`disposed_at` move from `ledger_assets` onto each
component too (Key Design Decision #50): a component added well after an
asset's original purchase (nachträgliche Anschaffungskosten — a genuine
value-increasing upgrade, not a repair, which is ordinary Erhaltungsaufwand
and never capitalized at all) must depreciate from *its own* acquisition
date, not retroactively from the asset's original one — a shared
asset-level date would back-date it, producing a wrong catch-up jump in AfA
the year it's added. Symmetrically, disposing of just one part of a
multi-component asset (e.g. a since-replaced graphics card) needs its own
disposal date, independent of the rest. Since every pre-existing asset here
only ever has exactly one component (this table doesn't exist before this
migration), the backfill is direct: each component's `acquisition_date` is
that one entry line's own `entry_date`, and `disposed_at` is copied straight
from the asset it came from.

Rather than dropping `entry_line_id`'s FK/UNIQUE constraint in place (the
original migration, 0012, never gave that FK an explicit name — so on a real
MariaDB database it's whatever name InnoDB auto-assigned at creation, which
this migration has no reliable way to know; see the drop-order lessons in
Key Design Decision #43), this rebuilds `ledger_assets` under a temporary
name and swaps it in, then drops the old table as a single atomic statement
(which needs no constraint name — see #43's own reasoning). No individual
column/constraint is ever dropped by name here.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("ledger_assets", "ledger_assets_pre49")

    op.create_table(
        "ledger_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("useful_life_years", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["category_id"], ["ledger_categories.id"], ondelete="RESTRICT"),
    )
    op.execute(
        """
        INSERT INTO ledger_assets (id, name, useful_life_years, category_id, notes)
        SELECT id, name, useful_life_years, category_id, notes
        FROM ledger_assets_pre49
        """
    )

    # Only created now, after the new ledger_assets rows above already exist —
    # asset_id's FK is valid immediately, no rename-across-a-live-FK risk.
    op.create_table(
        "ledger_asset_components",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("entry_line_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("acquisition_date", sa.Date(), nullable=False),
        sa.Column("disposed_at", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["asset_id"], ["ledger_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_line_id"], ["ledger_entry_lines.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_ledger_asset_components_asset_id", "ledger_asset_components", ["asset_id"])
    op.create_index("ix_ledger_asset_components_entry_line_id", "ledger_asset_components", ["entry_line_id"])
    op.execute(
        """
        INSERT INTO ledger_asset_components (asset_id, entry_line_id, amount, acquisition_date, disposed_at)
        SELECT id, entry_line_id, acquisition_cost, acquisition_date, disposed_at FROM ledger_assets_pre49
        """
    )

    # A single DROP TABLE removes entry_line_id's FK and UNIQUE constraint
    # together in one statement, on every backend — no constraint name needed.
    op.drop_table("ledger_assets_pre49")


def downgrade() -> None:
    # Best-effort, same convention as every other downgrade in this file that
    # generalized a 1:1 link into a many-to-many (see #16, #38): the old
    # single-line shape can't represent an asset with several components, so
    # this picks the first component (by id) as the sole line and sums every
    # component's amount into acquisition_cost — an asset built from more
    # than one purchase loses that split, but its total cost is preserved.
    # acquisition_date takes the earliest component's own date; disposed_at
    # is restored only if every component happens to share the exact same
    # disposal date (otherwise left NULL — there's no single date that
    # correctly represents a partial disposal in the old shape).
    op.rename_table("ledger_assets", "ledger_assets_post50")

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
    op.execute(
        """
        INSERT INTO ledger_assets (id, name, entry_line_id, acquisition_date, acquisition_cost,
                                    useful_life_years, category_id, disposed_at, notes)
        SELECT p.id, p.name,
               (SELECT c.entry_line_id FROM ledger_asset_components c
                WHERE c.asset_id = p.id ORDER BY c.id LIMIT 1),
               (SELECT MIN(c.acquisition_date) FROM ledger_asset_components c WHERE c.asset_id = p.id),
               (SELECT COALESCE(SUM(c.amount), 0) FROM ledger_asset_components c WHERE c.asset_id = p.id),
               p.useful_life_years, p.category_id,
               CASE WHEN (SELECT COUNT(*) FROM ledger_asset_components c
                          WHERE c.asset_id = p.id AND c.disposed_at IS NULL) = 0
                    THEN (SELECT MAX(c.disposed_at) FROM ledger_asset_components c WHERE c.asset_id = p.id)
                    ELSE NULL END,
               p.notes
        FROM ledger_assets_post50 p
        WHERE EXISTS (SELECT 1 FROM ledger_asset_components c WHERE c.asset_id = p.id)
        """
    )

    op.drop_table("ledger_asset_components")
    op.drop_table("ledger_assets_post50")
