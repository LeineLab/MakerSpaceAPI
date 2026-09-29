from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    pass


class FilamentBrand(Base):
    __tablename__ = "filament_brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    rolls: Mapped[list["FilamentRoll"]] = relationship("FilamentRoll", back_populates="brand")


class FilamentType(Base):
    __tablename__ = "filament_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    rolls: Mapped[list["FilamentRoll"]] = relationship("FilamentRoll", back_populates="type")


class FilamentRoll(Base):
    """One physical spool. Never deleted — Ausbuchen sets removed_at, mirroring
    rentals' rented_at/returned_at pattern, so a mistaken removal can be undone
    (POST /filament/rolls/{id}/restore) instead of re-creating the row.

    vendor_serial is the manufacturer-encoded identifier read from a spool's
    own NFC tag (Bambu Lab/Creality/Open3DTag) — deliberately NOT the NFC
    chip's own hardware UID, since a spool carries a tag on each side and
    those are two different physical chips with two different hardware UIDs
    for what is one physical roll; using the hardware UID would register two
    "different" rolls (or an Einbuchen followed by a spurious Ausbuchen) for
    a single scan session. OpenSpool tags carry no vendor serial at all, so
    vendor_serial is nullable — those rolls are only ever matched by spec
    (brand/type/weight/color) and the ESP32 station must ask the user
    explicitly whether to check in or out (no identity to auto-detect from).

    Uniqueness of vendor_serial among currently-in-stock rolls is enforced in
    the API layer, not a DB constraint — MariaDB has no portable partial
    unique index, same reasoning as the ledger module's category-keyword
    overlap check and cash-clearing-account exclusivity.
    """

    __tablename__ = "filament_rolls"
    __table_args__ = (
        Index("ix_filament_rolls_spec", "brand_id", "type_id", "weight_grams", "color"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("filament_brands.id", ondelete="RESTRICT"), nullable=False
    )
    type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("filament_types.id", ondelete="RESTRICT"), nullable=False
    )
    weight_grams: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=False)
    vendor_serial: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    added_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # OIDC sub, or "device:<slug>"
    removed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    removed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    brand: Mapped["FilamentBrand"] = relationship("FilamentBrand", back_populates="rolls")
    type: Mapped["FilamentType"] = relationship("FilamentType", back_populates="rolls")

    @property
    def in_stock(self) -> bool:
        return self.removed_at is None
