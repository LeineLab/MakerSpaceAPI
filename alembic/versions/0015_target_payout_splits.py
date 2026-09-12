"""replace ledger_entries.booking_target_payout_id (1:1) with a many-to-many
ledger_target_payout_entries table (split/bundle Kassen payout bookings)

The 1:1 UNIQUE column only ever let one Kassen payout map to exactly one
ledger entry. Real usage needs both directions: one payout split across
several bank transfers (a transfer-amount limit), and several payouts
bundled into one transfer. ledger_target_payout_entries(transaction_id,
entry_id, amount) replaces it — amount is the slice of that payout covered
by that entry. See Key Design Decision #38.

Existing 1:1 links are migrated into the new table before the old column is
dropped, so no booked-payout history is lost. The downgrade path is
best-effort: only entries that still have exactly one linked payout (and
that payout exactly one linked entry) are restorable to the old column —
this loses information for any split/bundle booked after upgrading, since
the old shape simply cannot represent them.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Idempotent/resumable: MySQL/MariaDB DDL auto-commits per statement (no
    # rollback on a later failure within this same migration), so a prior
    # failed attempt at this exact migration can leave the table/indexes/data
    # already in place while alembic_version is still stuck at the previous
    # revision. Every step below checks current state first so a retry after
    # a partial failure is safe, instead of dying on "already exists".
    if not inspector.has_table("ledger_target_payout_entries"):
        op.create_table(
            "ledger_target_payout_entries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("transaction_id", sa.Integer(), nullable=False),
            sa.Column("entry_id", sa.Integer(), nullable=False),
            sa.Column("amount", sa.Numeric(10, 2), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["entry_id"], ["ledger_entries.id"], ondelete="CASCADE"),
        )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("ledger_target_payout_entries")}
    if "ix_ledger_target_payout_entries_transaction_id" not in existing_indexes:
        op.create_index("ix_ledger_target_payout_entries_transaction_id", "ledger_target_payout_entries", ["transaction_id"])
    if "ix_ledger_target_payout_entries_entry_id" not in existing_indexes:
        op.create_index("ix_ledger_target_payout_entries_entry_id", "ledger_target_payout_entries", ["entry_id"])

    # NOT EXISTS guard so a retry never double-inserts rows a prior, partially
    # failed attempt already migrated.
    op.execute(
        """
        INSERT INTO ledger_target_payout_entries (transaction_id, entry_id, amount)
        SELECT le.booking_target_payout_id, le.id, -t.amount
        FROM ledger_entries le
        JOIN transactions t ON t.id = le.booking_target_payout_id
        WHERE le.booking_target_payout_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM ledger_target_payout_entries ltpe WHERE ltpe.entry_id = le.id
        )
        """
    )

    columns = {c["name"] for c in inspector.get_columns("ledger_entries")}
    if "booking_target_payout_id" not in columns:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.drop_constraint("uq_ledger_entries_booking_target_payout_id", type_="unique")
            batch_op.drop_constraint("fk_ledger_entries_booking_target_payout_id", type_="foreignkey")
            batch_op.drop_column("booking_target_payout_id")
    else:
        # MySQL/MariaDB (InnoDB) always requires an index covering a foreign
        # key's referencing column(s) to exist — the unique constraint's own
        # index is the only one covering booking_target_payout_id here, so
        # dropping it *before* the FK constraint that depends on it fails
        # with errno 1553 ("needed in a foreign key constraint"). The FK
        # must go first. (This is what actually broke the first deploy of
        # this migration: the table/indexes/data above had already committed
        # — MySQL DDL isn't transactional — before this step died.)
        op.drop_constraint("fk_ledger_entries_booking_target_payout_id", "ledger_entries", type_="foreignkey")
        op.drop_constraint("uq_ledger_entries_booking_target_payout_id", "ledger_entries", type_="unique")
        op.drop_column("ledger_entries", "booking_target_payout_id")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("ledger_entries") as batch_op:
            batch_op.add_column(sa.Column("booking_target_payout_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_ledger_entries_booking_target_payout_id", "transactions",
                ["booking_target_payout_id"], ["id"], ondelete="SET NULL",
            )
            batch_op.create_unique_constraint(
                "uq_ledger_entries_booking_target_payout_id", ["booking_target_payout_id"],
            )
    else:
        op.add_column("ledger_entries", sa.Column("booking_target_payout_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_ledger_entries_booking_target_payout_id", "ledger_entries", "transactions",
            ["booking_target_payout_id"], ["id"], ondelete="SET NULL",
        )
        op.create_unique_constraint(
            "uq_ledger_entries_booking_target_payout_id", "ledger_entries", ["booking_target_payout_id"],
        )

    # Best-effort: only restorable for entries/payouts that are still in a
    # clean 1:1 relationship — anything split or bundled after upgrading has
    # no representation in the old column and is left NULL.
    op.execute(
        """
        UPDATE ledger_entries
        SET booking_target_payout_id = (
            SELECT ltpe.transaction_id FROM ledger_target_payout_entries ltpe
            WHERE ltpe.entry_id = ledger_entries.id
        )
        WHERE (SELECT COUNT(*) FROM ledger_target_payout_entries ltpe WHERE ltpe.entry_id = ledger_entries.id) = 1
        AND (
            SELECT COUNT(*) FROM ledger_target_payout_entries ltpe2
            WHERE ltpe2.transaction_id = (
                SELECT ltpe3.transaction_id FROM ledger_target_payout_entries ltpe3 WHERE ltpe3.entry_id = ledger_entries.id
            )
        ) = 1
        """
    )

    # No explicit op.drop_index() first: both indexes cover a foreign key
    # (transaction_id, entry_id), and MariaDB/InnoDB requires an index
    # covering an FK's referencing column to exist at all times — dropping
    # one as its own statement while the FK constraint is still attached
    # fails with errno 1553 ("needed in a foreign key constraint"), exactly
    # the same class of bug as the upgrade()'s drop-order fix above.
    # op.drop_table() removes the table's indexes and constraints together
    # in one statement, so it never hits this.
    op.drop_table("ledger_target_payout_entries")
