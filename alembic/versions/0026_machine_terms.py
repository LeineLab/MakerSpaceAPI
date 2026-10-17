"""add machine_terms and machine_terms_acceptances

Per-machine, versioned terms of use plus a history of which user accepted
which version. A machine without any terms row has no restriction.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machine_terms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("machine_id", "version", name="uq_machine_terms_version"),
    )
    op.create_table(
        "machine_terms_acceptances",
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("machine_id", "user_id", "version"),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], onupdate="CASCADE", ondelete="RESTRICT"),
    )


def downgrade() -> None:
    op.drop_table("machine_terms_acceptances")
    op.drop_table("machine_terms")
