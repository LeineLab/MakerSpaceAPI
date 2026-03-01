from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device, require_admin_user
from app.database import get_db
from app.models.booking_target import BookingTarget
from app.models.machine import Machine
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.schemas.booking_target import (
    BookingTargetCreate,
    BookingTargetResponse,
    PayoutRequest,
    SetPinRequest,
    TargetTopupRequest,
    TopupRequest,
    TransferRequest,
)
from app.schemas.common import MessageResponse, TopupResponse
from app.schemas.transaction import TransactionResponse
from app.schemas.user import UserPinVerify

router = APIRouter()

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_target(slug: str, db: Session) -> BookingTarget:
    target = db.query(BookingTarget).filter(BookingTarget.slug == slug).first()
    if not target:
        raise HTTPException(status_code=404, detail=f"Booking target '{slug}' not found")
    return target


# --- Booking Targets ---

@router.get("/targets", response_model=list[BookingTargetResponse])
def list_targets(db: Session = Depends(get_db)):
    """Public: list all booking targets and their balances."""
    return db.query(BookingTarget).order_by(BookingTarget.name).all()


@router.post("/targets", response_model=BookingTargetResponse, status_code=201)
def create_target(
    body: BookingTargetCreate,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    if db.query(BookingTarget).filter(BookingTarget.slug == body.slug).first():
        raise HTTPException(status_code=409, detail="Slug already exists")
    target = BookingTarget(name=body.name, slug=body.slug, created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


# --- ATM operations ---

@router.post("/topup", response_model=TopupResponse)
def topup_user(
    body: TopupRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Add balance to user account and increase corresponding booking target."""
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    user = db.execute(
        select(User).where(User.id == body.nfc_id).with_for_update()
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    target = db.execute(
        select(BookingTarget).where(BookingTarget.slug == body.target_slug).with_for_update()
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail=f"Booking target '{body.target_slug}' not found")

    user.balance += body.amount
    target.balance += body.amount

    db.add(Transaction(
        user_id=body.nfc_id,
        amount=body.amount,
        type=TransactionType.topup,
        machine_id=device.id,
        target_id=target.id,
        note=f"Topup via {device.name}",
    ))
    db.commit()
    return {"detail": f"Topped up {body.amount} EUR. New balance: {user.balance} EUR", "balance": user.balance}


@router.post("/target-topup", response_model=MessageResponse)
def topup_target_only(
    body: TargetTopupRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Increase a booking target balance without crediting any user (e.g. cash donation)."""
    target = db.execute(
        select(BookingTarget).where(BookingTarget.slug == body.target_slug).with_for_update()
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail=f"Booking target '{body.target_slug}' not found")

    target.balance += body.amount
    db.add(Transaction(
        user_id=None,
        amount=body.amount,
        type=TransactionType.booking_target_topup,
        machine_id=device.id,
        target_id=target.id,
        note=body.note,
    ))
    db.commit()
    return {"detail": f"Target '{target.name}' balance increased by {body.amount} EUR"}


@router.get("/transactions/{nfc_id}", response_model=list[TransactionResponse])
def user_transactions(
    nfc_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Device endpoint: recent transaction history for a user."""
    return (
        db.query(Transaction)
        .filter(Transaction.user_id == nfc_id)
        .order_by(Transaction.created_at.desc())
        .limit(limit)
        .all()
    )


@router.post("/transfer", response_model=MessageResponse)
def transfer(
    body: TransferRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Transfer balance between two users."""
    if body.from_nfc_id == body.to_nfc_id:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same user")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Lock both rows in a consistent order to avoid deadlocks
    ids = sorted([body.from_nfc_id, body.to_nfc_id])
    users = {
        u.id: u
        for u in db.execute(
            select(User).where(User.id.in_(ids)).with_for_update()
        ).scalars().all()
    }

    sender = users.get(body.from_nfc_id)
    recipient = users.get(body.to_nfc_id)

    if not sender:
        raise HTTPException(status_code=404, detail="Sender not found")
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if sender.balance < body.amount:
        raise HTTPException(status_code=402, detail="Insufficient balance")

    sender.balance -= body.amount
    recipient.balance += body.amount

    now = datetime.now(UTC).replace(tzinfo=None)
    db.add(Transaction(
        user_id=body.from_nfc_id,
        amount=-body.amount,
        type=TransactionType.transfer_out,
        peer_user_id=body.to_nfc_id,
        note=body.note,
        machine_id=device.id,
        created_at=now,
    ))
    db.add(Transaction(
        user_id=body.to_nfc_id,
        amount=body.amount,
        type=TransactionType.transfer_in,
        peer_user_id=body.from_nfc_id,
        note=body.note,
        machine_id=device.id,
        created_at=now,
    ))
    db.commit()
    return {"detail": f"Transferred {body.amount} EUR from {body.from_nfc_id} to {body.to_nfc_id}"}


@router.post("/verify-pin", response_model=MessageResponse)
def verify_pin(
    body: UserPinVerify,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Verify a user's PIN without performing any payout. Returns 200 if valid, 403 if not."""
    user = db.query(User).filter(User.id == body.nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.pin_hash:
        raise HTTPException(status_code=403, detail="User has no PIN set")
    if not _pwd.verify(body.pin, user.pin_hash):
        raise HTTPException(status_code=403, detail="Invalid PIN")
    return {"detail": "PIN valid"}


@router.post("/payout", response_model=MessageResponse)
def payout(
    body: PayoutRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Payout from a booking target. Requires PIN verification."""
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    user = db.query(User).filter(User.id == body.nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.pin_hash:
        raise HTTPException(status_code=403, detail="User has no PIN set")
    if not _pwd.verify(body.pin, user.pin_hash):
        raise HTTPException(status_code=403, detail="Invalid PIN")

    target = db.execute(
        select(BookingTarget).where(BookingTarget.slug == body.target_slug).with_for_update()
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Booking target not found")
    if target.balance < body.amount:
        raise HTTPException(status_code=402, detail="Insufficient target balance")

    target.balance -= body.amount

    db.add(Transaction(
        user_id=body.nfc_id,
        amount=-body.amount,
        type=TransactionType.booking_target_payout,
        target_id=target.id,
        machine_id=device.id,
        note=body.note,
    ))
    db.commit()
    return {"detail": f"Payout of {body.amount} EUR from '{target.name}' successful"}


# --- PIN management ---

@router.post("/pin", response_model=MessageResponse)
def set_pin(
    body: SetPinRequest,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Set or update the PIN for a user's NFC card (admin only)."""
    user = db.query(User).filter(User.id == body.nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.pin_hash = _pwd.hash(body.pin)
    db.commit()
    return {"detail": "PIN updated"}


@router.delete("/pin/{nfc_id}", response_model=MessageResponse)
def clear_pin(
    nfc_id: int,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Clear (remove) the PIN for a user's NFC card (admin only)."""
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.pin_hash = None
    db.commit()
    return {"detail": "PIN cleared"}
