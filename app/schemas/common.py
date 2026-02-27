from decimal import Decimal

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    detail: str


class TopupResponse(BaseModel):
    detail: str
    balance: Decimal = Field(ge=0, examples=[Decimal("12.50")])
