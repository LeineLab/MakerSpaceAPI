"""add entry reversal, statement balance reconciliation, and CSV import mapping

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ledger_entries.reverses_entry_id — undo a booking via a reversal entry
    # (POST /ledger/entries/{id}/reverse) rather than editing/deleting one.
    # SQLite can't add a foreign key to an existing table outside of batch
    # mode (copy-and-swap); MariaDB/MySQL handles plain ADD COLUMN/ADD
    # CONSTRAINT directly — same dialect-branching pattern as 0003/0004/0006.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.add_column(sa.Column("reverses_entry_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_ledger_entries_reverses_entry_id", "ledger_entries", ["reverses_entry_id"], ["id"],
                ondelete="RESTRICT",
            )
    else:
        op.add_column("ledger_entries", sa.Column("reverses_entry_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_ledger_entries_reverses_entry_id", "ledger_entries", "ledger_entries",
            ["reverses_entry_id"], ["id"], ondelete="RESTRICT",
        )

    # ledger_import_batches: the bank's own reported opening/closing balance,
    # for reconciliation against the ledger's own computed balance
    # (GET /ledger/accounts/{id}/balance) — not compared automatically here.
    op.add_column("ledger_import_batches", sa.Column("statement_opening_balance", sa.Numeric(10, 2), nullable=True))
    op.add_column("ledger_import_batches", sa.Column("statement_closing_balance", sa.Numeric(10, 2), nullable=True))
    op.add_column("ledger_import_batches", sa.Column("statement_balance_date", sa.Date(), nullable=True))

    # bank_accounts.csv_mapping — remembered CSV column mapping per account
    # (banks have no standard CSV schema; the treasurer maps columns once).
    op.add_column("bank_accounts", sa.Column("csv_mapping", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("bank_accounts", "csv_mapping")

    op.drop_column("ledger_import_batches", "statement_balance_date")
    op.drop_column("ledger_import_batches", "statement_closing_balance")
    op.drop_column("ledger_import_batches", "statement_opening_balance")

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.drop_constraint("fk_ledger_entries_reverses_entry_id", type_="foreignkey")
            batch_op.drop_column("reverses_entry_id")
    else:
        op.drop_constraint("fk_ledger_entries_reverses_entry_id", "ledger_entries", type_="foreignkey")
        op.drop_column("ledger_entries", "reverses_entry_id")
