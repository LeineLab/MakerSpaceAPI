from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserAuthResponse(BaseModel):
    """Returned when a device authenticates an NFC card."""
    id: int
    name: Optional[str]
    balance: Decimal = Field(ge=0, examples=[Decimal("12.50")])
    has_pin: bool

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    """Create a new user (checkout box only)."""
    id: int  # NFC card UID
    name: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    name: Optional[str]
    oidc_sub: Optional[str]
    balance: Decimal = Field(ge=0, examples=[Decimal("12.50")])
    has_pin: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserBalanceAdjust(BaseModel):
    amount: Decimal = Field(examples=[Decimal("5.00")])
    note: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    oidc_sub: Optional[str] = None


class UserLinkOidc(BaseModel):
    oidc_sub: str


class UserSetPin(BaseModel):
    pin: str  # 4–8 digit PIN


class UserPinVerify(BaseModel):
    nfc_id: int
    pin: str


class LinkTokenResponse(BaseModel):
    url: str


class UserMeRentalResponse(BaseModel):
    rental_id: int
    item_name: str
    uhf_tid: str
    rented_at: datetime


class UserMeMachineResponse(BaseModel):
    machine_id: int
    machine_name: str
    machine_slug: str
    price_per_login: Decimal = Field(ge=0, examples=[Decimal("0.00")])
    price_per_minute: Decimal = Field(ge=0, examples=[Decimal("0.05")])
    booking_interval: int
