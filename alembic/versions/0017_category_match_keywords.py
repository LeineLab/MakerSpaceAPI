"""add ledger_categories.match_keywords for purpose-text autofill suggestions

A staging line's purpose_text (Verwendungszweck) is often a reliable signal
for its category — a Verein's "Mitgliedsbeiträge" category might always be
paid with the purpose text "Mitgliedsbeitrag", a "Spenden" category with
"Spende". match_keywords is a JSON list of terms (case-insensitive substring
match against purpose_text); GET /ledger/import/lines uses it to compute a
suggested_category_id per staging line, which the frontend pre-fills in the
booking modal (advisory only, never enforced — see Key Design Decision #45).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("ledger_categories")}
    if "match_keywords" not in columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("ledger_categories") as batch_op:
                batch_op.add_column(sa.Column("match_keywords", sa.JSON(), nullable=True))
        else:
            op.add_column("ledger_categories", sa.Column("match_keywords", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("ledger_categories")}
    if "match_keywords" in columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("ledger_categories") as batch_op:
                batch_op.drop_column("match_keywords")
        else:
            op.drop_column("ledger_categories", "match_keywords")
