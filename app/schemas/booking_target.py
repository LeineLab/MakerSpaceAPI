from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BookingTargetCreate(BaseModel):
    name: str
    slug: str


class BookingTargetResponse(BaseModel):
    id: int
    name: str
    slug: str
    balance: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TopupRequest(BaseModel):
    nfc_id: int
    amount: Decimal
    target_slug: str  # which booking target to credit


class TargetTopupRequest(BaseModel):
    """Increase target balance without touching a user's balance (e.g. donation)."""
    amount: Decimal
    target_slug: str
    note: Optional[str] = None


class TransferRequest(BaseModel):
    from_nfc_id: int
    to_nfc_id: int
    amount: Decimal
    note: Optional[str] = None


class PayoutRequest(BaseModel):
    nfc_id: int
    pin: str
    target_slug: str
    amount: Decimal
    note: Optional[str] = None


class SetPinRequest(BaseModel):
    nfc_id: int
    pin: str  # plaintext, will be hashed
