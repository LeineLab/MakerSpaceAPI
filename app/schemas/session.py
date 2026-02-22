from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class SessionCreate(BaseModel):
    nfc_id: int


class SessionResponse(BaseModel):
    id: int
    machine_id: int
    user_id: int
    start_time: datetime
    end_time: Optional[datetime]
    paid_until: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionCreateResponse(BaseModel):
    session_id: int
    start_time: datetime
    paid_until: datetime
    remaining_seconds: float
    max_seconds: Optional[float]  # None = no limit (free machine)


class SessionExtendResponse(BaseModel):
    session_id: int
    paid_until: Optional[datetime]
    remaining_seconds: float
    max_seconds: Optional[float]  # None = no limit (free machine)
    terminated: bool = False
