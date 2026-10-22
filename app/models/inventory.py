from datetime import UTC, date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class InventoryLocation(Base):
    """A physical place stock is kept (soldering station, first-aid kit, ...).

    The slug is generated once at creation and never changes on rename, so a
    QR code or bookmark pointing at /inventory/<slug> stays valid.
    """

    __tablename__ = "inventory_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_checked_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # OIDC sub
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    items: Mapped[list["InventoryItem"]] = relationship(
        "InventoryItem", back_populates="location", order_by="InventoryItem.name"
    )


class InventoryItem(Base):
    """One stocked thing at one location, with a target count (target_qty).

    Without track_expiry the current count is `quantity` directly. With
    track_expiry it is the sum of the item's batches and `quantity` stays 0, so
    there is only ever one place the count lives (see `on_hand`).
    """

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory_locations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    target_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    track_expiry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    location: Mapped["InventoryLocation"] = relationship("InventoryLocation", back_populates="items")
    batches: Mapped[list["InventoryBatch"]] = relationship(
        "InventoryBatch",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="InventoryBatch.expires_on",
    )

    @property
    def on_hand(self) -> int:
        if self.track_expiry:
            return sum(b.quantity for b in self.batches)
        return self.quantity


class InventoryBatch(Base):
    """A quantity of a track_expiry item that shares one best-before date.
    expires_on NULL = no date known (e.g. converted from an untracked count)."""

    __tablename__ = "inventory_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    item: Mapped["InventoryItem"] = relationship("InventoryItem", back_populates="batches")
