from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.ledger import LedgerCategory
    from app.models.transaction import Transaction


class BookingTarget(Base):
    __tablename__ = "booking_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    # The EÜR category a payout from this target is booked against by default
    # (e.g. "Kasse Spenden" -> "Spenden") — see Key Design Decision #34. Only
    # a default: POST /ledger/target-payouts/{id}/book can still override it.
    default_category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ledger_categories.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", back_populates="target"
    )
    default_category: Mapped[Optional["LedgerCategory"]] = relationship("LedgerCategory")
