"""add ledger module (bank accounts, categories, journal entries, bank import staging)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("iban", sa.String(34), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_offline", sa.Boolean(), nullable=False),
        sa.Column("tracked", sa.Boolean(), nullable=False),
        sa.Column("opening_balance", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("iban"),
        sa.CheckConstraint(
            "(is_offline = 0 AND iban IS NOT NULL) OR (is_offline = 1 AND iban IS NULL)",
            name="ck_bank_account_offline_has_no_iban",
        ),
    )
    op.create_index("ix_bank_accounts_iban", "bank_accounts", ["iban"])

    op.create_table(
        "ledger_categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("kind", sa.Enum("income", "expense", name="ledgercategorykind"), nullable=False),
        sa.Column(
            "sphere",
            sa.Enum(
                "ideell", "vermoegensverwaltung", "zweckbetrieb",
                "wirtschaftlicher_geschaeftsbetrieb", name="ledgersphere",
            ),
            nullable=True,  # only required when LEDGER_SPHERES_ENABLED (app-layer check)
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_ledger_categories_slug", "ledger_categories", ["slug"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("paperless_document_id", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_entry_date", "ledger_entries", ["entry_date"])

    op.create_table(
        "ledger_entry_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["entry_id"], ["ledger_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["ledger_categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(bank_account_id IS NOT NULL AND category_id IS NULL) OR "
            "(bank_account_id IS NULL AND category_id IS NOT NULL)",
            name="ck_ledger_entry_line_one_side",
        ),
    )
    op.create_index("ix_ledger_entry_lines_entry_id", "ledger_entry_lines", ["entry_id"])

    op.create_table(
        "ledger_import_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Enum("file", "fints", name="ledgerimportsource"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("imported_by", sa.String(255), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_import_batches_bank_account_id", "ledger_import_batches", ["bank_account_id"])

    op.create_table(
        "ledger_import_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("purpose_text", sa.String(500), nullable=True),
        sa.Column("counterparty_name", sa.String(255), nullable=True),
        sa.Column("counterparty_iban", sa.String(34), nullable=True),
        sa.Column("bank_reference", sa.String(100), nullable=True),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("new", "duplicate", "booked", "ignored", name="ledgerimportstatus"),
            nullable=False,
        ),
        sa.Column("matched_entry_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["ledger_import_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["matched_entry_id"], ["ledger_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_import_lines_batch_id", "ledger_import_lines", ["batch_id"])
    op.create_index("ix_ledger_import_lines_bank_account_id", "ledger_import_lines", ["bank_account_id"])
    op.create_index("ix_ledger_import_lines_dedup_hash", "ledger_import_lines", ["dedup_hash"])
    op.create_index("ix_ledger_import_lines_status", "ledger_import_lines", ["status"])
    op.create_index(
        "ix_ledger_import_lines_account_reference", "ledger_import_lines",
        ["bank_account_id", "bank_reference"],
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_import_lines_account_reference", table_name="ledger_import_lines")
    op.drop_index("ix_ledger_import_lines_status", table_name="ledger_import_lines")
    op.drop_index("ix_ledger_import_lines_dedup_hash", table_name="ledger_import_lines")
    op.drop_index("ix_ledger_import_lines_bank_account_id", table_name="ledger_import_lines")
    op.drop_index("ix_ledger_import_lines_batch_id", table_name="ledger_import_lines")
    op.drop_table("ledger_import_lines")
    op.drop_index("ix_ledger_import_batches_bank_account_id", table_name="ledger_import_batches")
    op.drop_table("ledger_import_batches")
    op.drop_index("ix_ledger_entry_lines_entry_id", table_name="ledger_entry_lines")
    op.drop_table("ledger_entry_lines")
    op.drop_index("ix_ledger_entries_entry_date", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_ledger_categories_slug", table_name="ledger_categories")
    op.drop_table("ledger_categories")
    op.drop_index("ix_bank_accounts_iban", table_name="bank_accounts")
    op.drop_table("bank_accounts")
