from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.transaction import TransactionType


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    amount: Decimal
    type: TransactionType
    machine_id: Optional[int]
    product_id: Optional[int]
    session_id: Optional[int]
    target_id: Optional[int]
    peer_user_id: Optional[int]
    note: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
