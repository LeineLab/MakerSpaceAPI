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


class DenominationEntry(BaseModel):
    """One denomination bucket: `count` deposits of the same `amount`."""
    amount: Decimal = Field(examples=[Decimal("50.00")])
    count: int = Field(examples=[13])
    sum: Decimal = Field(examples=[Decimal("650.00")])


class TargetDenominations(BaseModel):
    """Denomination breakdown for a single booking target since its last payout."""
    id: int
    name: str
    slug: str
    last_payout: Optional[datetime] = None  # None = target was never skimmed
    denominations: list[DenominationEntry]
    total: Decimal = Field(examples=[Decimal("664.00")])


class CombinedDenominations(BaseModel):
    """Aggregate across all targets, each counted since its own last payout."""
    denominations: list[DenominationEntry]
    total: Decimal = Field(examples=[Decimal("664.00")])


class DenominationReport(BaseModel):
    combined: CombinedDenominations
    targets: list[TargetDenominations]
