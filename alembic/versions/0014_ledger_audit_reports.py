"""add ledger_audit_reports table (Kassenprüfungsprotokolle)

Records the Verein's Kassenprüfung (annual or ad-hoc audit) — period
covered, auditor names (free text, not tied to `users`), findings, and
whether the Kassenprüfer recommend the Vorstand's Entlastung. Writing these
is gated by a new, narrower permission (admin or explicit auditor-group
membership, not a plain treasurer) — see Key Design Decision #37.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-12 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ledger_audit_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("audit_date", sa.Date(), nullable=False),
        sa.Column("auditors", sa.String(255), nullable=False),
        sa.Column("findings", sa.String(4000), nullable=True),
        sa.Column("recommends_discharge", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paperless_document_id", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ledger_audit_reports")
