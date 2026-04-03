from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from passlib.context import CryptContext
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device, require_admin_user, require_checkout_device, require_session_user
from app.auth.jwt import create_link_token
from app.config import settings
from app.database import get_db
from app.models.machine import Machine, MachineAuthorization
from app.models.rental import Rental
from app.models.session import MachineSession
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.transaction import MeTransactionResponse
from app.schemas.user import (
    LinkTokenResponse, UserAuthResponse, UserCreate, UserLinkOidc,
    UserMeMachineResponse, UserMeRentalResponse, UserMeSessionResponse,
    UserResponse, UserUpdate,
)

router = APIRouter()

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Self-service: GET /users/me and sub-resources
# ---------------------------------------------------------------------------

def _me_user(user: dict, db: Session) -> User:
    """Return the DB user for the current OIDC session, or raise 404."""
    db_user = db.query(User).filter(User.oidc_sub == user.get("sub")).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="No NFC card linked to your account")
    return db_user


@router.get("/me", response_model=UserResponse)
def get_me(
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    """Return the current user's own profile (requires a linked NFC card)."""
    return _me_user(user, db)


@router.delete("/me/oidc", response_model=MessageResponse)
def unlink_me_oidc(
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    """Unlink the current OIDC account from its NFC card. The card record is kept."""
    db_user = _me_user(user, db)
    db_user.oidc_sub = None
    db.commit()
    return {"message": "Card unlinked successfully"}


@router.get("/me/transactions", response_model=list[MeTransactionResponse])
def get_me_transactions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    """Transaction history for the currently logged-in user."""
    db_user = _me_user(user, db)
    txns = (
        db.query(Transaction)
        .filter(Transaction.user_id == db_user.id)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        MeTransactionResponse(
            id=t.id,
            amount=t.amount,
            type=t.type,
            note=t.note,
            machine_name=t.machine.name if t.machine else None,
            created_at=t.created_at,
        )
        for t in txns
    ]


@router.get("/me/rentals", response_model=list[UserMeRentalResponse])
def get_me_rentals(
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    """Currently rented items for the logged-in user."""
    db_user = _me_user(user, db)
    rentals = (
        db.query(Rental)
        .filter(Rental.user_id == db_user.id, Rental.returned_at.is_(None))
        .order_by(Rental.rented_at.desc())
        .all()
    )
    return [
        UserMeRentalResponse(
            rental_id=r.id,
            item_name=r.item.name,
            uhf_tid=r.item.uhf_tid,
            rented_at=r.rented_at,
        )
        for r in rentals
    ]


@router.get("/me/machines", response_model=list[UserMeMachineResponse])
def get_me_machines(
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    """Machines the logged-in user is authorized to use."""
    db_user = _me_user(user, db)
    auths = (
        db.query(MachineAuthorization)
        .join(Machine, Machine.id == MachineAuthorization.machine_id)
        .filter(MachineAuthorization.user_id == db_user.id, Machine.active.is_(True))
        .order_by(Machine.name)
        .all()
    )
    return [
        UserMeMachineResponse(
            machine_id=a.machine.id,
            machine_name=a.machine.name,
            machine_slug=a.machine.slug,
            price_per_login=a.price_per_login,
            price_per_minute=a.price_per_minute,
            booking_interval=a.booking_interval,
        )
        for a in auths
    ]


@router.get("/me/sessions", response_model=list[UserMeSessionResponse])
def get_me_sessions(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    """Machine session history for the logged-in user, newest first."""
    db_user = _me_user(user, db)
    sessions = (
        db.query(MachineSession)
        .filter(MachineSession.user_id == db_user.id)
        .order_by(MachineSession.start_time.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    costs: dict[int, Decimal] = {}
    session_ids = [s.id for s in sessions]
    if session_ids:
        for sid, total in (
            db.query(Transaction.session_id, func.sum(Transaction.amount))
            .filter(Transaction.session_id.in_(session_ids))
            .group_by(Transaction.session_id)
            .all()
        ):
            costs[sid] = Decimal(str(total or 0))

    result = []
    for s in sessions:
        duration = int((s.end_time - s.start_time).total_seconds()) if s.end_time else None
        cost = max(-costs.get(s.id, Decimal("0.00")), Decimal("0.00"))
        result.append(UserMeSessionResponse(
            id=s.id,
            machine_name=s.machine.name,
            machine_slug=s.machine.slug,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_seconds=duration,
            total_cost=cost,
        ))
    return result


# ---------------------------------------------------------------------------
# Device endpoints
# ---------------------------------------------------------------------------

@router.post("/{nfc_id}/connect-link", response_model=LinkTokenResponse)
def generate_connect_link(
    nfc_id: int,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Generate a short-lived OIDC linking URL for an NFC card (device token required)."""
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.oidc_sub:
        raise HTTPException(status_code=409, detail="Card already linked to an OIDC account")
    token = create_link_token(nfc_id)
    return {"url": f"{settings.BASE_URL}/auth/connect/{token}"}


@router.get("/nfc/{nfc_id}", response_model=UserAuthResponse)
def authenticate_nfc(
    nfc_id: int,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Authenticate a user by NFC card UID. Returns name and balance."""
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("", response_model=UserResponse, status_code=201)
def create_user(
    body: UserCreate,
    device: Machine = Depends(require_checkout_device),
    db: Session = Depends(get_db),
):
    """Create a new user by NFC card UID (checkout devices only)."""
    existing = db.query(User).filter(User.id == body.id).first()
    if existing:
        raise HTTPException(status_code=409, detail="User with this NFC ID already exists")
    user = User(id=body.id, name=body.name, created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("", response_model=list[UserResponse])
def list_users(
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """List all users (admin only)."""
    return db.query(User).order_by(User.created_at).all()


@router.get("/{nfc_id}", response_model=UserResponse)
def get_user(
    nfc_id: int,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.put("/{nfc_id}", response_model=UserResponse)
def update_user(
    nfc_id: int,
    body: UserUpdate,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Update user name and/or OIDC sub (admin only). Send empty string to unlink OIDC."""
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.name is not None:
        user.name = body.name or None
    if body.oidc_sub is not None:
        new_sub = body.oidc_sub.strip() or None
        if new_sub:
            existing = db.query(User).filter(User.oidc_sub == new_sub, User.id != nfc_id).first()
            if existing:
                raise HTTPException(status_code=409, detail="OIDC sub already linked to another user")
        user.oidc_sub = new_sub
    db.commit()
    db.refresh(user)
    return user


@router.put("/{nfc_id}/oidc", response_model=UserResponse)
def link_oidc(
    nfc_id: int,
    body: UserLinkOidc,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Link an OIDC sub claim to a user card."""
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    oidc_sub = body.oidc_sub
    existing = db.query(User).filter(User.oidc_sub == oidc_sub, User.id != nfc_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="OIDC sub already linked to another user")
    user.oidc_sub = oidc_sub
    db.commit()
    db.refresh(user)
    return user
