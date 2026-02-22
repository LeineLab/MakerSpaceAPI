"""initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- No-dependency tables ---

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("oidc_sub", sa.String(255), nullable=True),
        sa.Column("balance", sa.Numeric(10, 2), nullable=False),
        sa.Column("pin_hash", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("oidc_sub"),
    )
    op.create_index("ix_users_oidc_sub", "users", ["oidc_sub"])

    op.create_table(
        "machines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("api_token_hash", sa.String(64), nullable=False),
        sa.Column("machine_type", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        sa.UniqueConstraint("api_token_hash"),
    )
    op.create_index("ix_machines_slug", "machines", ["slug"])

    op.create_table(
        "booking_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("balance", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_booking_targets_slug", "booking_targets", ["slug"])

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ean", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ean"),
    )
    op.create_index("ix_products_ean", "products", ["ean"])
    op.create_index("ix_products_category", "products", ["category"])
    op.create_index("ix_products_category_name", "products", ["category", "name"])

    op.create_table(
        "rental_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("uhf_tid", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uhf_tid"),
    )
    op.create_index("ix_rental_items_uhf_tid", "rental_items", ["uhf_tid"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"])

    # --- Tables with foreign keys ---

    op.create_table(
        "machine_admin_groups",
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("oidc_group", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("machine_id", "oidc_group"),
    )

    op.create_table(
        "machine_authorizations",
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("price_per_login", sa.Numeric(10, 2), nullable=False),
        sa.Column("price_per_minute", sa.Numeric(10, 2), nullable=False),
        sa.Column("booking_interval", sa.Integer(), nullable=False),
        sa.Column("granted_by", sa.String(255), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], onupdate="CASCADE", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("machine_id", "user_id"),
    )

    op.create_table(
        "machine_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("paid_until", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], onupdate="CASCADE", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sessions_machine_user_start", "machine_sessions",
        ["machine_id", "user_id", "start_time"],
    )

    op.create_table(
        "product_aliases",
        sa.Column("ean", sa.String(20), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ean"),
    )
    op.create_index("ix_product_aliases_product_id", "product_aliases", ["product_id"])

    op.create_table(
        "product_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.String(255), nullable=False),
        sa.Column(
            "change_type",
            sa.Enum(
                "created", "price_change", "stock_add", "stock_deduct",
                "category_change", "name_change", "activated", "deactivated",
                "stocktaking",
                name="productaudittype",
            ),
            nullable=False,
        ),
        sa.Column("old_value", sa.String(255), nullable=True),
        sa.Column("new_value", sa.String(255), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_audit_product_id", "product_audit", ["product_id"])

    op.create_table(
        "rental_permissions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by", sa.String(255), nullable=True),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], onupdate="CASCADE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "rentals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("rented_at", sa.DateTime(), nullable=False),
        sa.Column("returned_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["rental_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], onupdate="CASCADE", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rentals_item_returned", "rentals", ["item_id", "returned_at"])
    op.create_index("ix_rentals_user_returned", "rentals", ["user_id", "returned_at"])

    # transactions last — references almost every other table
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "purchase", "topup", "transfer_out", "transfer_in",
                "machine_login", "machine_usage",
                "booking_target_topup", "booking_target_payout",
                "admin_adjustment",
                name="transactiontype",
            ),
            nullable=False,
        ),
        sa.Column("machine_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("peer_user_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["machine_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_id"], ["booking_targets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], onupdate="CASCADE", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["peer_user_id"], ["users.id"], onupdate="CASCADE", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_type", "transactions", ["type"])
    op.create_index("ix_transactions_user_date", "transactions", ["user_id", "created_at"])
    op.create_index("ix_transactions_type_date", "transactions", ["type", "created_at"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("rentals")
    op.drop_table("rental_permissions")
    op.drop_table("product_audit")
    op.drop_table("product_aliases")
    op.drop_table("machine_sessions")
    op.drop_table("machine_authorizations")
    op.drop_table("machine_admin_groups")
    op.drop_table("audit_log")
    op.drop_table("rental_items")
    op.drop_table("products")
    op.drop_table("booking_targets")
    op.drop_table("machines")
    op.drop_table("users")
