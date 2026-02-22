"""add product_categories table

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_categories",
        sa.Column("name", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    # Seed from existing products so nothing breaks on upgrade
    conn = op.get_bind()
    rows = conn.execute(text("SELECT DISTINCT category FROM products")).fetchall()
    if rows:
        conn.execute(
            text("INSERT INTO product_categories (name) VALUES (:name)"),
            [{"name": r[0]} for r in rows],
        )


def downgrade() -> None:
    op.drop_table("product_categories")
