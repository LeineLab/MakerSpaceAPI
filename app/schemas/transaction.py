from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.transaction import TransactionType


class TransactionResponse(BaseModel):
    id: int
    user_id: Optional[int]
    amount: Decimal = Field(examples=[Decimal("1.50")])
    type: TransactionType
    machine_id: Optional[int]
    product_id: Optional[int]
    session_id: Optional[int]
    target_id: Optional[int]
    peer_user_id: Optional[int]
    note: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeTransactionResponse(BaseModel):
    """Enriched transaction for the self-service /users/me/transactions endpoint."""
    id: int
    amount: Decimal = Field(examples=[Decimal("1.50")])
    type: TransactionType
    note: Optional[str]
    machine_name: Optional[str]
    created_at: datetime
