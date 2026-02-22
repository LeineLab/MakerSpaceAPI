import enum
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.booking_target import BookingTarget
    from app.models.machine import Machine
    from app.models.product import Product
    from app.models.session import MachineSession
    from app.models.user import User


class TransactionType(str, enum.Enum):
    purchase = "purchase"
    topup = "topup"
    transfer_out = "transfer_out"
    transfer_in = "transfer_in"
    machine_login = "machine_login"
    machine_usage = "machine_usage"
    booking_target_topup = "booking_target_topup"
    booking_target_payout = "booking_target_payout"
    admin_adjustment = "admin_adjustment"


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_date", "user_id", "created_at"),
        Index("ix_transactions_type_date", "type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType), nullable=False, index=True
    )
    machine_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("machine_sessions.id", ondelete="SET NULL"), nullable=True
    )
    target_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("booking_targets.id", ondelete="SET NULL"), nullable=True
    )
    peer_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="transactions"
    )
    machine: Mapped[Optional["Machine"]] = relationship(
        "Machine", back_populates="transactions"
    )
    product: Mapped[Optional["Product"]] = relationship("Product", back_populates="transactions")
    session: Mapped[Optional["MachineSession"]] = relationship(
        "MachineSession", back_populates="transactions"
    )
    target: Mapped[Optional["BookingTarget"]] = relationship(
        "BookingTarget", back_populates="transactions"
    )
    peer_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[peer_user_id])
