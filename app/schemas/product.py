from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.product import ProductAuditType


class ProductCreate(BaseModel):
    ean: str
    name: str
    price: Decimal = Field(ge=0, examples=[Decimal("1.50")])
    stock: int = 0
    category: str


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[Decimal] = Field(default=None, ge=0, examples=[Decimal("1.50")])
    category: Optional[str] = None
    active: Optional[bool] = None


class ProductStockAdjust(BaseModel):
    delta: int  # positive = add, negative = deduct
    note: Optional[str] = None


class ProductStocktaking(BaseModel):
    count: int
    note: Optional[str] = None


class ProductAliasCreate(BaseModel):
    ean: str


class ProductAliasResponse(BaseModel):
    ean: str
    product_id: int

    model_config = ConfigDict(from_attributes=True)


class ProductResponse(BaseModel):
    id: int
    ean: str
    name: str
    price: Decimal = Field(ge=0, examples=[Decimal("1.50")])
    stock: int
    category: str
    active: bool

    model_config = ConfigDict(from_attributes=True)


class ProductDetailResponse(ProductResponse):
    aliases: list[ProductAliasResponse]


class ProductAuditResponse(BaseModel):
    id: int
    product_id: int
    changed_by: str
    change_type: ProductAuditType
    old_value: Optional[str]
    new_value: Optional[str]
    note: Optional[str]
    changed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CategoryCreate(BaseModel):
    name: str


class PurchaseBody(BaseModel):
    nfc_id: int


class PurchaseResponse(BaseModel):
    detail: str
    product: str
    new_balance: Decimal = Field(ge=0, examples=[Decimal("4.20")])


class ProductPopularityResponse(BaseModel):
    product_id: int
    ean: str
    name: str
    purchase_count: int
    days: int
