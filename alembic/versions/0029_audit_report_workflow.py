"""audit report draft/final workflow, form fields, entry receipt markers, account closing date

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-23 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bank_accounts") as batch:
        batch.add_column(sa.Column("closed_on", sa.Date(), nullable=True))

    with op.batch_alter_table("ledger_entries") as batch:
        batch.add_column(sa.Column("receipt_status", sa.String(20), nullable=True))
        batch.add_column(sa.Column("receipt_note", sa.String(500), nullable=True))

    with op.batch_alter_table("ledger_audit_reports") as batch:
        batch.add_column(sa.Column("status", sa.String(10), nullable=False, server_default="draft"))
        batch.add_column(sa.Column("finalized_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("finalized_by", sa.String(255), nullable=True))
        batch.add_column(sa.Column("frozen_stats", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("supplements_report_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("has_objections", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("receipts_complete", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("statements_complete", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("receipts_clearly_marked", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("receipts_comment", sa.String(1000), nullable=True))
        batch.add_column(sa.Column("next_audit_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("remarks", sa.String(2000), nullable=True))
        batch.add_column(sa.Column("place", sa.String(100), nullable=True))
        batch.create_foreign_key(
            "fk_audit_report_supplements", "ledger_audit_reports", ["supplements_report_id"], ["id"], ondelete="RESTRICT"
        )

    # Existing reports that already recorded findings count as having objections.
    op.execute(
        "UPDATE ledger_audit_reports SET has_objections = TRUE "
        "WHERE findings IS NOT NULL AND findings <> ''"
    )


def downgrade() -> None:
    with op.batch_alter_table("ledger_audit_reports") as batch:
        batch.drop_constraint("fk_audit_report_supplements", type_="foreignkey")
        for col in (
            "place", "remarks", "next_audit_date", "receipts_comment", "receipts_clearly_marked",
            "statements_complete", "receipts_complete", "has_objections", "supplements_report_id",
            "frozen_stats", "finalized_by", "finalized_at", "status",
        ):
            batch.drop_column(col)
    with op.batch_alter_table("ledger_entries") as batch:
        batch.drop_column("receipt_note")
        batch.drop_column("receipt_status")
    with op.batch_alter_table("bank_accounts") as batch:
        batch.drop_column("closed_on")
