"""add ledger_reserves and ledger_reserve_movements tables (§62 AO Rücklagen)

Tracks Rücklagen (frei/zweckgebunden/betriebsmittel/wiederbeschaffung) as a
logical allocation on top of already-recognized surplus — not a real cash
movement, so these tables are never joined against bank_accounts/
ledger_entries. Feeds the new Mittelverwendungsrechnung report. See Key
Design Decision #36.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ledger_reserves",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.Enum("frei", "zweckgebunden", "betriebsmittel", "wiederbeschaffung", name="ledgerreservekind"), nullable=False),
        sa.Column("sphere", sa.Enum("ideell", "vermoegensverwaltung", "zweckbetrieb", "wirtschaftlicher_geschaeftsbetrieb", name="ledgersphere"), nullable=True),
        sa.Column("purpose", sa.String(255), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ledger_reserve_movements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reserve_id", sa.Integer(), nullable=False),
        sa.Column("movement_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["reserve_id"], ["ledger_reserves.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_ledger_reserve_movements_reserve_id", "ledger_reserve_movements", ["reserve_id"]
    )


def downgrade() -> None:
    # No explicit op.drop_index() first: reserve_id has a foreign key, and
    # MariaDB/InnoDB requires an index covering it to exist at all times —
    # dropping the index as its own statement while the FK constraint is
    # still attached fails with errno 1553 ("needed in a foreign key
    # constraint"). op.drop_table() removes the table's indexes and
    # constraints together in one statement, so it never hits this.
    op.drop_table("ledger_reserve_movements")
    op.drop_table("ledger_reserves")
    # MariaDB creates ENUMs inline (no separate type to drop); PostgreSQL would
    # need explicit sa.Enum(...).drop(op.get_bind()) here, but this project
    # only targets MariaDB/SQLite (see Technology Stack in CLAUDE.md).
