"""add booking_targets.default_category_id, ledger_entries.booking_target_payout_id, bank_accounts.is_cash_clearing_account

Bridges the legacy NFC-Kassen system (booking_targets/transactions) into the
ledger. A target carries a default EÜR category (e.g. "Kasse Spenden" ->
"Spenden") used purely at EÜR-computation time — cash arriving in a target
(topup/target-topup/adjustment) is never written as its own ledger_entries
row, only aggregated on read, since it's the same money already tracked by
booking_targets/transactions (see Key Design Decision #34). A payout (cash
physically leaving a target) still IS booked, manually, as a transfer from
the one shared "Kassenbestand" clearing account (bank_accounts.
is_cash_clearing_account, at most one True at a time) to whichever real
account the treasurer actually deposited it into — linked back via
booking_target_payout_id (UNIQUE) so the same payout can't be double-booked.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("booking_targets") as batch_op:
            batch_op.add_column(sa.Column("default_category_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_booking_targets_default_category_id", "ledger_categories",
                ["default_category_id"], ["id"], ondelete="SET NULL",
            )
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.add_column(sa.Column("booking_target_payout_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_ledger_entries_booking_target_payout_id", "transactions",
                ["booking_target_payout_id"], ["id"], ondelete="SET NULL",
            )
            batch_op.create_unique_constraint(
                "uq_ledger_entries_booking_target_payout_id", ["booking_target_payout_id"],
            )
        with op.batch_alter_table("bank_accounts") as batch_op:
            batch_op.add_column(sa.Column(
                "is_cash_clearing_account", sa.Boolean(), nullable=False, server_default=sa.false(),
            ))
    else:
        op.add_column("booking_targets", sa.Column("default_category_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_booking_targets_default_category_id", "booking_targets", "ledger_categories",
            ["default_category_id"], ["id"], ondelete="SET NULL",
        )
        op.add_column("ledger_entries", sa.Column("booking_target_payout_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_ledger_entries_booking_target_payout_id", "ledger_entries", "transactions",
            ["booking_target_payout_id"], ["id"], ondelete="SET NULL",
        )
        op.create_unique_constraint(
            "uq_ledger_entries_booking_target_payout_id", "ledger_entries", ["booking_target_payout_id"],
        )
        op.add_column("bank_accounts", sa.Column(
            "is_cash_clearing_account", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))


def downgrade() -> None:
    op.drop_column("bank_accounts", "is_cash_clearing_account")

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.drop_constraint("uq_ledger_entries_booking_target_payout_id", type_="unique")
            batch_op.drop_constraint("fk_ledger_entries_booking_target_payout_id", type_="foreignkey")
            batch_op.drop_column("booking_target_payout_id")
        with op.batch_alter_table("booking_targets") as batch_op:
            batch_op.drop_constraint("fk_booking_targets_default_category_id", type_="foreignkey")
            batch_op.drop_column("default_category_id")
    else:
        # MySQL/MariaDB (InnoDB) requires an index covering a foreign key's
        # referencing column(s) to exist at all times — the unique
        # constraint's own index is the only one covering
        # booking_target_payout_id, so it must be dropped only after the FK
        # that depends on it, or this fails with errno 1553 ("needed in a
        # foreign key constraint"). See the same fix/explanation in 0015's
        # upgrade(), which hit this for real in production.
        op.drop_constraint("fk_ledger_entries_booking_target_payout_id", "ledger_entries", type_="foreignkey")
        op.drop_constraint("uq_ledger_entries_booking_target_payout_id", "ledger_entries", type_="unique")
        op.drop_column("ledger_entries", "booking_target_payout_id")
        op.drop_constraint("fk_booking_targets_default_category_id", "booking_targets", type_="foreignkey")
        op.drop_column("booking_targets", "default_category_id")
