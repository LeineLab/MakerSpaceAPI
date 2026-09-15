"""add ledger_sync_tokens table (external FinTS-sync script auth, #52)

Raised by the user as a follow-up to the manual FinTS wizard: automated,
scheduled bank sync without storing bank login/PIN anywhere in this app.
Rather than a server-side encrypted credential store (which would put real
banking credentials inside the always-on, internet-facing app process — a
much higher-value target than anything else here), the credentials stay
entirely on a host the treasurer trusts, run via the new standalone
scripts/ledger_fints_sync.py. This table only holds a narrowly-scoped bearer
token (hashed at rest, same convention as machines.api_token_hash) that can
query one bank account's last-imported-transaction date and submit newly
fetched transactions into the existing staging/dedup pipeline — nothing
else, and never anything that reveals the token itself once issued.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ledger_sync_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["bank_account_id"], ["bank_accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_ledger_sync_tokens_token_hash"),
    )
    op.create_index("ix_ledger_sync_tokens_bank_account_id", "ledger_sync_tokens", ["bank_account_id"])


def downgrade() -> None:
    # A single DROP TABLE removes the index/FK/UNIQUE together in one
    # statement on every backend — no separate op.drop_index() needed (and
    # actively wrong on MariaDB while the FK is still attached, see #43).
    op.drop_table("ledger_sync_tokens")
