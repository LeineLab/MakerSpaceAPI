from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class RentalItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    uhf_tid: str  # hex string, max 40 chars


class RentalItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class RentalItemResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    uhf_tid: str
    active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RentalItemStatusResponse(BaseModel):
    uhf_tid: str
    item_name: str
    is_rented: bool
    rental_id: Optional[int]
    rented_by_user_id: Optional[int]
    rented_by_name: Optional[str]
    rented_at: Optional[datetime]


class RentRequest(BaseModel):
    nfc_id: int
    uhf_tid: str


class ReturnRequest(BaseModel):
    uhf_tid: str


class RentalResponse(BaseModel):
    id: int
    item_id: int
    user_id: int
    rented_at: datetime
    returned_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ActiveRentalResponse(BaseModel):
    rental_id: int
    item_id: int
    item_name: str
    uhf_tid: str
    user_id: int
    user_name: Optional[str]
    rented_at: datetime


class RentalPermissionResponse(BaseModel):
    user_id: int
    user_name: Optional[str]
    granted_by: Optional[str]
    granted_at: datetime


class RentalCatalogItem(BaseModel):
    uhf_tid: str
    name: str
    is_rented: bool
