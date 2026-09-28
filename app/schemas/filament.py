from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class FilamentBrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Creality"])


class FilamentBrandResponse(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class FilamentTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Hyper PLA RFID"])


class FilamentTypeResponse(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class FilamentRollCreate(BaseModel):
    """Manual entry via the web UI — brand/type reference already-existing rows."""
    brand_id: int
    type_id: int
    weight_grams: int = Field(gt=0, examples=[1000])
    color: str = Field(min_length=1, max_length=50, examples=["Schwarz"])
    vendor_serial: Optional[str] = Field(default=None, max_length=64)
    quantity: int = Field(default=1, ge=1, le=100)


class FilamentRollUpdate(BaseModel):
    brand_id: Optional[int] = None
    type_id: Optional[int] = None
    weight_grams: Optional[int] = Field(default=None, gt=0)
    color: Optional[str] = Field(default=None, min_length=1, max_length=50)
    vendor_serial: Optional[str] = Field(default=None, max_length=64)


class FilamentRollResponse(BaseModel):
    id: int
    brand_id: int
    brand_name: str
    type_id: int
    type_name: str
    weight_grams: int
    color: str
    vendor_serial: Optional[str]
    added_at: datetime
    added_by: Optional[str]
    removed_at: Optional[datetime]
    removed_by: Optional[str]
    in_stock: bool

    model_config = ConfigDict(from_attributes=True)


class FilamentStockSummary(BaseModel):
    """One row per distinct spec (brand+type+weight+color) with its current in-stock count."""
    brand_id: int
    brand_name: str
    type_id: int
    type_name: str
    weight_grams: int
    color: str
    count_in_stock: int


# ---------------------------------------------------------------------------
# Device (ESP32 filament station) endpoints
# ---------------------------------------------------------------------------

class FilamentScanRequest(BaseModel):
    """Data read off a spool's NFC tag. brand_name/type_name are auto-created
    if they don't exist yet. vendor_serial is omitted/null for tag formats
    that carry no manufacturer serial (e.g. OpenSpool) — the station must
    then ask the user explicitly whether to check in or out."""
    brand_name: str = Field(min_length=1, max_length=100)
    type_name: str = Field(min_length=1, max_length=100)
    weight_grams: int = Field(gt=0)
    color: str = Field(min_length=1, max_length=50)
    vendor_serial: Optional[str] = Field(default=None, max_length=64)


class FilamentScanResponse(BaseModel):
    """identify_required=True means vendor_serial was absent and no action was
    taken — the station must show an Einbuchen/Ausbuchen choice to the user
    and then call /filament/checkin or /filament/checkout explicitly."""
    identify_required: bool
    action: Optional[str] = None  # "checked_in" | "checked_out", set iff identify_required is False
    roll: Optional[FilamentRollResponse] = None
    current_stock: Optional[int] = None  # matching spec's in-stock count, set iff identify_required is True


class FilamentManualActionRequest(BaseModel):
    """Explicit check-in/check-out, used both by the OpenSpool no-serial device
    flow (after the user picks an action) and reusable for a serialized tag
    if ever needed directly."""
    brand_name: str = Field(min_length=1, max_length=100)
    type_name: str = Field(min_length=1, max_length=100)
    weight_grams: int = Field(gt=0)
    color: str = Field(min_length=1, max_length=50)
    vendor_serial: Optional[str] = Field(default=None, max_length=64)


class FilamentStatusRequest(BaseModel):
    """Read-only lookup for a serial's current in-stock status — lets the
    station show an explicit Einbuchen/Ausbuchen confirmation before acting,
    instead of /scan's deterministic auto-action. Never modifies anything."""
    vendor_serial: str = Field(min_length=1, max_length=64)


class FilamentStatusResponse(BaseModel):
    in_stock: bool
    roll: Optional[FilamentRollResponse] = None
