"""add reviewed_by/reviewed_at to ledger_entries (Kassenprüfung checkoff, #56)

Raised by the user directly: an authorized person (Kassenprüfer) should be
able to "abhaken" (tick off) an individual booking as checked during a
Kassenprüfung, recording who checked it and when, and see that on the
entry's own detail view. Modeled as two plain nullable columns directly on
ledger_entries — the simplest shape for a binary checked/unchecked flag with
attribution, in the same spirit as ledger_entries.reverses_entry_id already
living directly on the entry rather than in a side table. Not part of the
immutable financial audit trail itself (entries are still never edited/
deleted for their financial content) — this is a review annotation on top,
freely toggleable, same "not a financial event" precedent as
ledger_reserve_movements/ledger_audit_reports (see Key Design Decision #56).

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ledger_entries", sa.Column("reviewed_by", sa.String(255), nullable=True))
    op.add_column("ledger_entries", sa.Column("reviewed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ledger_entries") as batch_op:
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
