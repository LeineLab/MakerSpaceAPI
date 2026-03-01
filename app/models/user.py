from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.machine import MachineAuthorization
    from app.models.rental import Rental, RentalPermission
    from app.models.session import MachineSession
    from app.models.transaction import Transaction


class User(Base):
    __tablename__ = "users"

    # NFC card UID stored as integer (4–7 bytes, fits in signed BigInteger)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    oidc_sub: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    pin_hash: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )

    # Relationships
    machine_authorizations: Mapped[list["MachineAuthorization"]] = relationship(
        "MachineAuthorization", back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["MachineSession"]] = relationship(
        "MachineSession", back_populates="user"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        primaryjoin="User.id == Transaction.user_id",
        back_populates="user",
    )
    rental_permission: Mapped[Optional["RentalPermission"]] = relationship(
        "RentalPermission", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    rentals: Mapped[list["Rental"]] = relationship("Rental", back_populates="user")

    @property
    def has_pin(self) -> bool:
        return self.pin_hash is not None
