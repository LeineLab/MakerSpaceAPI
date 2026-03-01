from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device
from app.database import get_db
from app.models.machine import Machine, MachineAuthorization
from app.models.session import MachineSession
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.schemas.session import (
    SessionCreate,
    SessionCreateResponse,
    SessionExtendResponse,
)
from app.schemas.common import MessageResponse

router = APIRouter()


def _calc_max_seconds(
    balance: Decimal, price_per_minute: Decimal, remaining_seconds: float
) -> float | None:
    """Return total usable seconds, or None if the machine is free (no limit)."""
    if price_per_minute <= 0:
        return None
    additional_minutes = float(balance / price_per_minute)
    return remaining_seconds + additional_minutes * 60


@router.post("", response_model=SessionCreateResponse, status_code=201)
def create_session(
    body: SessionCreate,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """
    Start a machine session for the given NFC user.

    Deducts: price_per_login + price_per_minute * booking_interval
    Requires: balance >= that amount
    """
    # Lock user row for atomic balance check
    user = db.execute(
        select(User).where(User.id == body.nfc_id).with_for_update()
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    auth = (
        db.query(MachineAuthorization)
        .filter(
            MachineAuthorization.machine_id == device.id,
            MachineAuthorization.user_id == body.nfc_id,
        )
        .first()
    )
    if not auth:
        raise HTTPException(status_code=403, detail="User not authorized for this machine")

    cost = auth.price_per_login + auth.price_per_minute * auth.booking_interval
    if user.balance < cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient balance. Required: {cost}, available: {user.balance}",
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    paid_until = now + timedelta(minutes=auth.booking_interval)

    user.balance -= cost

    session = MachineSession(
        machine_id=device.id,
        user_id=body.nfc_id,
        start_time=now,
        paid_until=paid_until,
    )
    db.add(session)
    db.flush()  # get session.id

    # Record transactions
    if auth.price_per_login > 0:
        db.add(Transaction(
            user_id=body.nfc_id,
            amount=-auth.price_per_login,
            type=TransactionType.machine_login,
            machine_id=device.id,
            session_id=session.id,
            note="Login fee",
        ))
    if auth.price_per_minute > 0:
        db.add(Transaction(
            user_id=body.nfc_id,
            amount=-(auth.price_per_minute * auth.booking_interval),
            type=TransactionType.machine_usage,
            machine_id=device.id,
            session_id=session.id,
            note=f"First {auth.booking_interval}min interval",
        ))

    db.commit()
    db.refresh(session)

    remaining_seconds = (paid_until - now).total_seconds()
    max_seconds = _calc_max_seconds(user.balance, auth.price_per_minute, remaining_seconds)

    return SessionCreateResponse(
        session_id=session.id,
        start_time=session.start_time,
        paid_until=session.paid_until,
        remaining_seconds=remaining_seconds,
        max_seconds=max_seconds,
    )


@router.put("/{session_id}", response_model=SessionExtendResponse)
def extend_session(
    session_id: int,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """
    Called by device in regular intervals to extend or check a session.

    - If paid_until is still in the future: return remaining time, no charge.
    - If paid_until is in the past: try to charge for another interval.
      - Insufficient balance → terminate session, return HTTP 402.
    """
    session = (
        db.query(MachineSession)
        .filter(MachineSession.id == session_id, MachineSession.machine_id == device.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.is_active:
        raise HTTPException(status_code=409, detail="Session already terminated")

    auth = (
        db.query(MachineAuthorization)
        .filter(
            MachineAuthorization.machine_id == device.id,
            MachineAuthorization.user_id == session.user_id,
        )
        .first()
    )
    if not auth:
        raise HTTPException(status_code=500, detail="Authorization record missing")

    now = datetime.now(UTC).replace(tzinfo=None)
    remaining_seconds = (session.paid_until - now).total_seconds()

    if remaining_seconds > 0:
        # Already paid — just return status
        user = db.query(User).filter(User.id == session.user_id).first()
        max_seconds = _calc_max_seconds(user.balance, auth.price_per_minute, remaining_seconds)
        return SessionExtendResponse(
            session_id=session.id,
            paid_until=session.paid_until,
            remaining_seconds=remaining_seconds,
            max_seconds=max_seconds,
            terminated=False,
        )

    # paid_until is in the past — try to extend
    user = db.execute(
        select(User).where(User.id == session.user_id).with_for_update()
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=500, detail="User record missing")

    interval_cost = auth.price_per_minute * auth.booking_interval

    if user.balance < interval_cost:
        # Terminate session
        session.end_time = now
        db.commit()
        raise HTTPException(
            status_code=402,
            detail="Insufficient balance — session terminated",
        )

    session.paid_until += timedelta(minutes=auth.booking_interval)

    if interval_cost > 0:
        user.balance -= interval_cost
        db.add(Transaction(
            user_id=session.user_id,
            amount=-interval_cost,
            type=TransactionType.machine_usage,
            machine_id=device.id,
            session_id=session.id,
            note=f"Extended {auth.booking_interval}min interval",
        ))
    db.commit()
    remaining_seconds = (session.paid_until - now).total_seconds()

    max_seconds = _calc_max_seconds(user.balance, auth.price_per_minute, remaining_seconds)

    return SessionExtendResponse(
        session_id=session.id,
        paid_until=session.paid_until,
        remaining_seconds=remaining_seconds,
        max_seconds=max_seconds,
        terminated=False,
    )


@router.delete("/{session_id}", response_model=MessageResponse)
def terminate_session(
    session_id: int,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Terminate a session (device-initiated logout)."""
    session = (
        db.query(MachineSession)
        .filter(MachineSession.id == session_id, MachineSession.machine_id == device.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.is_active:
        return {"detail": "Session was already terminated"}
    session.end_time = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {"detail": "Session terminated"}
