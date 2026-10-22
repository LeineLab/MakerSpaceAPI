from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryLocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Erste-Hilfe-Kasten Werkstatt"])
    notes: Optional[str] = Field(default=None, max_length=255)


class InventoryLocationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    notes: Optional[str] = Field(default=None, max_length=255)


class InventoryLocationResponse(BaseModel):
    id: int
    name: str
    slug: str
    notes: Optional[str]
    last_checked_at: Optional[datetime]
    last_checked_by: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class InventoryItemCreate(BaseModel):
    location_id: int
    name: str = Field(min_length=1, max_length=100, examples=["Steriler Wundverband"])
    unit: Optional[str] = Field(default=None, max_length=20, examples=["Stk."])
    target_qty: int = Field(default=0, ge=0)
    track_expiry: bool = False
    # Starting count. Without track_expiry it is the count itself; with it, it
    # becomes one batch carrying expires_on (if given).
    quantity: int = Field(default=0, ge=0)
    expires_on: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=255)


class InventoryItemUpdate(BaseModel):
    """Switching track_expiry converts the stock: off->on turns the count into
    one undated batch, on->off sums the batches into the count."""
    location_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    unit: Optional[str] = Field(default=None, max_length=20)
    target_qty: Optional[int] = Field(default=None, ge=0)
    track_expiry: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=255)


class InventoryQuantityUpdate(BaseModel):
    """Exactly one of `quantity` (absolute) or `delta` (relative; safe when two
    people count at once)."""
    quantity: Optional[int] = Field(default=None, ge=0)
    delta: Optional[int] = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.quantity is None) == (self.delta is None):
            raise ValueError("Provide exactly one of quantity or delta")
        return self


class InventoryBatchCreate(BaseModel):
    quantity: int = Field(ge=1)
    expires_on: Optional[date] = None


class InventoryBatchUpdate(BaseModel):
    """quantity 0 removes the batch."""
    quantity: Optional[int] = Field(default=None, ge=0)
    expires_on: Optional[date] = None
    clear_expires_on: bool = False


class InventoryBatchResponse(BaseModel):
    id: int
    quantity: int
    expires_on: Optional[date]
    # "ok" | "expiring" | "expired"
    state: str


class InventoryItemResponse(BaseModel):
    id: int
    location_id: int
    name: str
    unit: Optional[str]
    target_qty: int
    track_expiry: bool
    notes: Optional[str]
    on_hand: int          # everything physically there, expired included
    usable_qty: int       # on_hand minus expired
    expired_qty: int
    expiring_soon_qty: int
    to_order: int         # what to buy: target minus what is usable and not about to expire
    batches: list[InventoryBatchResponse]


class InventoryShoppingRow(BaseModel):
    item: InventoryItemResponse
    location_name: str
    location_slug: str


class InventorySummary(BaseModel):
    attention_items: int  # items on the shopping list
