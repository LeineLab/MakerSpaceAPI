"""add ledger_entries.review_note — Kassenprüfung discrepancy notes

A follow-up to the Kassenprüfung checkoff: besides just ticking a booking
off as checked, an auditor can attach a free-text note when something looks
off ("falls eine Unstimmigkeit auffällt"), independent of the
checked/unchecked state (a discrepancy note may exist on an entry that's
deliberately left unreviewed pending follow-up, or on one that's already
been checked off with a remark attached). VARCHAR(500), the same width as
ledger_entries.description/ledger_entry_lines.note.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ledger_entries", sa.Column("review_note", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ledger_entries") as batch_op:
        batch_op.drop_column("review_note")
