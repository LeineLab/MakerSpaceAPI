"""widen ledger_entries.description and ledger_entry_lines.note to 500 chars

Real bug found in production, 2026-09-13: booking a staging line auto-fills
the resulting entry's description and the bank leg's note straight from the
staging line's own purpose_text (ledger_import_lines.purpose_text, already
VARCHAR(500)) — but description/note were both only VARCHAR(255). A long,
entirely genuine bank purpose text (e.g. a SEPA-Rücklastschrift's verbose
Rückgabegrund message, easily past 255 chars) overflowed the shorter column
on MariaDB with a 500 Internal Server Error (errno 1406, "Data too long for
column 'description'") — not on SQLite, which never enforces VARCHAR length
at all, which is why this was invisible to the test suite. Widened both to
500 to match purpose_text's own limit, so it can always be copied in full.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.alter_column(
                "description", existing_type=sa.String(255), type_=sa.String(500), existing_nullable=False,
            )
        with op.batch_alter_table("ledger_entry_lines") as batch_op:
            batch_op.alter_column(
                "note", existing_type=sa.String(255), type_=sa.String(500), existing_nullable=True,
            )
    else:
        op.alter_column(
            "ledger_entries", "description", existing_type=sa.String(255), type_=sa.String(500), existing_nullable=False,
        )
        op.alter_column(
            "ledger_entry_lines", "note", existing_type=sa.String(255), type_=sa.String(500), existing_nullable=True,
        )


def downgrade() -> None:
    # Best-effort, same convention as every other downgrade in this file: if
    # a value longer than 255 chars was saved in the meantime, shrinking the
    # column back would fail on MariaDB (the exact error this migration
    # exists to fix) — no attempt is made to truncate existing data first.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.alter_column(
                "description", existing_type=sa.String(500), type_=sa.String(255), existing_nullable=False,
            )
        with op.batch_alter_table("ledger_entry_lines") as batch_op:
            batch_op.alter_column(
                "note", existing_type=sa.String(500), type_=sa.String(255), existing_nullable=True,
            )
    else:
        op.alter_column(
            "ledger_entries", "description", existing_type=sa.String(500), type_=sa.String(255), existing_nullable=False,
        )
        op.alter_column(
            "ledger_entry_lines", "note", existing_type=sa.String(500), type_=sa.String(255), existing_nullable=True,
        )
