"""move paperless_document_id from ledger_entries to ledger_entry_lines

A journal entry's single Paperless-Dokument-ID couldn't represent several
invoices paid in one bank debit (one entry, several category lines) — only
one of the invoices' documents could ever be linked. Moving the field onto
each ledger_entry_lines row lets every category line carry its own document,
while a plain single-receipt booking (the common case, one category line)
still just has one document as before. Existing values are copied onto every
category-side line of their entry — correct for the old "one receipt split
across categories" case (same document on every resulting line) and a
reasonable starting point for a genuinely multi-invoice entry booked before
this migration (which the treasurer can now split apart per line if needed).

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {c["name"] for c in inspector.get_columns("ledger_entry_lines")}
    if "paperless_document_id" not in columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("ledger_entry_lines") as batch_op:
                batch_op.add_column(sa.Column("paperless_document_id", sa.String(64), nullable=True))
        else:
            op.add_column("ledger_entry_lines", sa.Column("paperless_document_id", sa.String(64), nullable=True))

    op.execute(
        """
        UPDATE ledger_entry_lines
        SET paperless_document_id = (
            SELECT le.paperless_document_id FROM ledger_entries le WHERE le.id = ledger_entry_lines.entry_id
        )
        WHERE category_id IS NOT NULL
        AND paperless_document_id IS NULL
        AND EXISTS (
            SELECT 1 FROM ledger_entries le
            WHERE le.id = ledger_entry_lines.entry_id AND le.paperless_document_id IS NOT NULL
        )
        """
    )

    entry_columns = {c["name"] for c in inspector.get_columns("ledger_entries")}
    if "paperless_document_id" not in entry_columns:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.drop_column("paperless_document_id")
    else:
        op.drop_column("ledger_entries", "paperless_document_id")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {c["name"] for c in inspector.get_columns("ledger_entries")}
    if "paperless_document_id" not in columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("ledger_entries") as batch_op:
                batch_op.add_column(sa.Column("paperless_document_id", sa.String(64), nullable=True))
        else:
            op.add_column("ledger_entries", sa.Column("paperless_document_id", sa.String(64), nullable=True))

    # Best-effort, same spirit as every other downgrade in this file: the old
    # single-document-per-entry shape can't represent several distinct
    # documents on one entry's lines, so this just picks the first non-null
    # one found (arbitrary tie-break, MIN() over the line ids) rather than
    # losing the field's data entirely.
    op.execute(
        """
        UPDATE ledger_entries
        SET paperless_document_id = (
            SELECT lel.paperless_document_id FROM ledger_entry_lines lel
            WHERE lel.entry_id = ledger_entries.id AND lel.paperless_document_id IS NOT NULL
            ORDER BY lel.id LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM ledger_entry_lines lel
            WHERE lel.entry_id = ledger_entries.id AND lel.paperless_document_id IS NOT NULL
        )
        """
    )

    entry_line_columns = {c["name"] for c in inspector.get_columns("ledger_entry_lines")}
    if "paperless_document_id" not in entry_line_columns:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entry_lines") as batch_op:
            batch_op.drop_column("paperless_document_id")
    else:
        op.drop_column("ledger_entry_lines", "paperless_document_id")
