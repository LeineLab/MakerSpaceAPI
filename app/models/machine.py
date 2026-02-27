from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.session import MachineSession
    from app.models.transaction import Transaction
    from app.models.user import User


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    api_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    machine_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="machine"
    )  # machine | checkout | bankomat | rental_station
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    admin_users: Mapped[list["MachineAdmin"]] = relationship(
        "MachineAdmin", back_populates="machine", cascade="all, delete-orphan"
    )
    authorizations: Mapped[list["MachineAuthorization"]] = relationship(
        "MachineAuthorization", back_populates="machine", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["MachineSession"]] = relationship(
        "MachineSession", back_populates="machine"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", back_populates="machine"
    )


class MachineAdmin(Base):
    __tablename__ = "machine_admins"

    machine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True
    )
    oidc_sub: Mapped[str] = mapped_column(String(255), primary_key=True)

    machine: Mapped["Machine"] = relationship("Machine", back_populates="admin_users")


class MachineAuthorization(Base):
    __tablename__ = "machine_authorizations"

    machine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
        primary_key=True,
    )
    price_per_login: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    price_per_minute: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    booking_interval: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )  # minutes
    granted_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    machine: Mapped["Machine"] = relationship("Machine", back_populates="authorizations")
    user: Mapped["User"] = relationship("User", back_populates="machine_authorizations")
