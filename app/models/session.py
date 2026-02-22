from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.machine import Machine
    from app.models.transaction import Transaction
    from app.models.user import User


class MachineSession(Base):
    __tablename__ = "machine_sessions"
    __table_args__ = (
        Index("ix_sessions_machine_user_start", "machine_id", "user_id", "start_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    paid_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    machine: Mapped["Machine"] = relationship("Machine", back_populates="sessions")
    user: Mapped["User"] = relationship("User", back_populates="sessions")
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", back_populates="session"
    )

    @property
    def is_active(self) -> bool:
        return self.end_time is None
