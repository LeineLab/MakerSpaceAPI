from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class UserAuthResponse(BaseModel):
    """Returned when a device authenticates an NFC card."""
    id: int
    name: Optional[str]
    balance: Decimal

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    """Create a new user (checkout box only)."""
    id: int  # NFC card UID
    name: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    name: Optional[str]
    oidc_sub: Optional[str]
    balance: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserBalanceAdjust(BaseModel):
    amount: Decimal
    note: Optional[str] = None


class UserLinkOidc(BaseModel):
    oidc_sub: str


class UserSetPin(BaseModel):
    pin: str  # 4–8 digit PIN


class UserPinVerify(BaseModel):
    nfc_id: int
    pin: str
