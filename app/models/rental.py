from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class RentalItem(Base):
    __tablename__ = "rental_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # UHF RFID TID: up to 160 bits = 20 bytes = 40 hex chars
    uhf_tid: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    rentals: Mapped[list["Rental"]] = relationship("Rental", back_populates="item")


class RentalPermission(Base):
    __tablename__ = "rental_permissions"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user: Mapped["User"] = relationship("User", back_populates="rental_permission")


class Rental(Base):
    __tablename__ = "rentals"
    __table_args__ = (
        Index("ix_rentals_item_returned", "item_id", "returned_at"),
        Index("ix_rentals_user_returned", "user_id", "returned_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_items.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    rented_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    returned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    item: Mapped["RentalItem"] = relationship("RentalItem", back_populates="rentals")
    user: Mapped["User"] = relationship("User", back_populates="rentals")

    @property
    def is_active(self) -> bool:
        return self.returned_at is None
