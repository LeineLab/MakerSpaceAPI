import enum
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class ProductAuditType(str, enum.Enum):
    created = "created"
    price_change = "price_change"
    stock_add = "stock_add"
    stock_deduct = "stock_deduct"
    category_change = "category_change"
    name_change = "name_change"
    activated = "activated"
    deactivated = "deactivated"
    stocktaking = "stocktaking"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (Index("ix_products_category_name", "category", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ean: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    aliases: Mapped[list["ProductAlias"]] = relationship(
        "ProductAlias", back_populates="product", cascade="all, delete-orphan"
    )
    audit_entries: Mapped[list["ProductAudit"]] = relationship(
        "ProductAudit", back_populates="product", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", back_populates="product"
    )


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    ean: Mapped[str] = mapped_column(String(20), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )

    product: Mapped["Product"] = relationship("Product", back_populates="aliases")


class ProductAudit(Base):
    __tablename__ = "product_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    changed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    change_type: Mapped[ProductAuditType] = mapped_column(Enum(ProductAuditType), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    product: Mapped["Product"] = relationship("Product", back_populates="audit_entries")
