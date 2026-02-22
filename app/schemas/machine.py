from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class MachineCreate(BaseModel):
    name: str
    slug: str
    machine_type: str = "machine"  # machine | checkout | bankomat | rental_station


class MachineUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    machine_type: Optional[str] = None
    active: Optional[bool] = None


class MachineResponse(BaseModel):
    id: int
    name: str
    slug: str
    machine_type: str
    active: bool
    created_at: datetime
    created_by: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class MachineCreateResponse(MachineResponse):
    """Includes plaintext token — shown only once."""
    api_token: str


class MachineAdminGroupCreate(BaseModel):
    oidc_group: str


class MachineAdminGroupResponse(BaseModel):
    machine_id: int
    oidc_group: str

    model_config = ConfigDict(from_attributes=True)


class AuthorizationCreate(BaseModel):
    nfc_id: int
    price_per_login: Decimal = Decimal("0.00")
    price_per_minute: Decimal = Decimal("0.00")
    booking_interval: int = 60  # minutes


class AuthorizationUpdate(BaseModel):
    price_per_login: Optional[Decimal] = None
    price_per_minute: Optional[Decimal] = None
    booking_interval: Optional[int] = None


class AuthorizationResponse(BaseModel):
    machine_id: int
    user_id: int
    price_per_login: Decimal
    price_per_minute: Decimal
    booking_interval: int
    granted_by: Optional[str]
    granted_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthorizeUserResponse(BaseModel):
    """Response from GET /machines/{slug}/authorize/{nfc_id}"""
    authorized: bool
    user_id: int
    user_name: Optional[str]
    balance: Decimal
    price_per_login: Decimal
    price_per_minute: Decimal
    booking_interval: int  # minutes
