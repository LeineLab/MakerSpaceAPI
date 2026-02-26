from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device, require_admin_user, require_checkout_device
from app.database import get_db
from app.models.machine import Machine
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.user import UserAuthResponse, UserCreate, UserResponse, UserUpdate

router = APIRouter()

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
    body: dict,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Link an OIDC sub claim to a user card."""
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    oidc_sub = body.get("oidc_sub")
    if not oidc_sub:
        raise HTTPException(status_code=400, detail="oidc_sub is required")
    existing = db.query(User).filter(User.oidc_sub == oidc_sub, User.id != nfc_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="OIDC sub already linked to another user")
    user.oidc_sub = oidc_sub
    db.commit()
    db.refresh(user)
    return user
