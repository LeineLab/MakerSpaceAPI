from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BookingTargetCreate(BaseModel):
    name: str
    slug: str


class BookingTargetResponse(BaseModel):
    id: int
    name: str
    slug: str
    balance: Decimal = Field(examples=[Decimal("42.00")])
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TopupRequest(BaseModel):
    nfc_id: int
    amount: Decimal = Field(gt=0, examples=[Decimal("10.00")])
    target_slug: str  # which booking target to credit


class TargetTopupRequest(BaseModel):
    """Increase target balance without touching a user's balance (e.g. donation)."""
    amount: Decimal = Field(gt=0, examples=[Decimal("10.00")])
    target_slug: str
    note: Optional[str] = None


class TransferRequest(BaseModel):
    from_nfc_id: int
    to_nfc_id: int
    amount: Decimal = Field(gt=0, examples=[Decimal("5.00")])
    note: Optional[str] = None


class PayoutRequest(BaseModel):
    nfc_id: int
    pin: str
    target_slug: str
    amount: Decimal = Field(gt=0, examples=[Decimal("20.00")])
    note: Optional[str] = None


class SetPinRequest(BaseModel):
    nfc_id: int
    pin: str  # plaintext, will be hashed
