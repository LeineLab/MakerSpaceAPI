from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device, require_admin_user, require_machine_manager
from app.auth.tokens import generate_api_token
from app.database import get_db
from app.models.machine import Machine, MachineAdminGroup, MachineAuthorization
from app.models.user import User
from app.schemas.machine import (
    AuthorizationCreate,
    AuthorizationResponse,
    AuthorizationUpdate,
    AuthorizeUserResponse,
    MachineAdminGroupCreate,
    MachineAdminGroupResponse,
    MachineCreate,
    MachineCreateResponse,
    MachineResponse,
    MachineUpdate,
)
from app.schemas.common import MessageResponse

router = APIRouter()


@router.get("", response_model=list[MachineResponse])
def list_machines(
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    return db.query(Machine).order_by(Machine.name).all()


@router.post("", response_model=MachineCreateResponse, status_code=201)
def register_machine(
    body: MachineCreate,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Register a new machine. Returns the API token once — store it safely."""
    if db.query(Machine).filter(Machine.slug == body.slug).first():
        raise HTTPException(status_code=409, detail="Slug already in use")
    plaintext_token, token_hash = generate_api_token()
    machine = Machine(
        name=body.name,
        slug=body.slug,
        machine_type=body.machine_type,
        api_token_hash=token_hash,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        created_by=admin.get("sub"),
    )
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return {**MachineResponse.model_validate(machine).model_dump(), "api_token": plaintext_token}


@router.get("/{slug}", response_model=MachineResponse)
def get_machine(
    slug: str,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machine = db.query(Machine).filter(Machine.slug == slug).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    return machine


@router.put("/{slug}", response_model=MachineResponse)
def update_machine(
    slug: str,
    body: MachineUpdate,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machine = db.query(Machine).filter(Machine.slug == slug).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    if body.name is not None:
        machine.name = body.name
    if body.slug is not None:
        if db.query(Machine).filter(Machine.slug == body.slug, Machine.id != machine.id).first():
            raise HTTPException(status_code=409, detail="Slug already in use")
        machine.slug = body.slug
    if body.machine_type is not None:
        machine.machine_type = body.machine_type
    if body.active is not None:
        machine.active = body.active
    db.commit()
    db.refresh(machine)
    return machine


@router.delete("/{slug}", response_model=MessageResponse)
def deactivate_machine(
    slug: str,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machine = db.query(Machine).filter(Machine.slug == slug).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    machine.active = False
    db.commit()
    return {"detail": "Machine deactivated"}


@router.post("/{slug}/token", response_model=MachineCreateResponse)
def regenerate_token(
    slug: str,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Regenerate the API token for a machine. Previous token is immediately invalidated."""
    machine = db.query(Machine).filter(Machine.slug == slug).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    plaintext_token, token_hash = generate_api_token()
    machine.api_token_hash = token_hash
    db.commit()
    db.refresh(machine)
    return {**MachineResponse.model_validate(machine).model_dump(), "api_token": plaintext_token}


# --- Admin groups ---

@router.get("/{slug}/admin-groups", response_model=list[MachineAdminGroupResponse])
def list_admin_groups(
    slug: str,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machine = db.query(Machine).filter(Machine.slug == slug).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    return machine.admin_groups


@router.post("/{slug}/admin-groups", response_model=MachineAdminGroupResponse, status_code=201)
def add_admin_group(
    slug: str,
    body: MachineAdminGroupCreate,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machine = db.query(Machine).filter(Machine.slug == slug).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    existing = (
        db.query(MachineAdminGroup)
        .filter(
            MachineAdminGroup.machine_id == machine.id,
            MachineAdminGroup.oidc_group == body.oidc_group,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Group already added")
    group = MachineAdminGroup(machine_id=machine.id, oidc_group=body.oidc_group)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{slug}/admin-groups/{oidc_group}", response_model=MessageResponse)
def remove_admin_group(
    slug: str,
    oidc_group: str,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machine = db.query(Machine).filter(Machine.slug == slug).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    group = (
        db.query(MachineAdminGroup)
        .filter(
            MachineAdminGroup.machine_id == machine.id,
            MachineAdminGroup.oidc_group == oidc_group,
        )
        .first()
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    db.delete(group)
    db.commit()
    return {"detail": "Group removed"}


# --- Authorizations ---

@router.get("/{slug}/authorizations", response_model=list[AuthorizationResponse])
def list_authorizations(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _, machine = require_machine_manager(slug, request, db)
    return machine.authorizations


@router.post("/{slug}/authorizations", response_model=AuthorizationResponse, status_code=201)
def grant_authorization(
    slug: str,
    body: AuthorizationCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    user_info, machine = require_machine_manager(slug, request, db)
    user = db.query(User).filter(User.id == body.nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    existing = (
        db.query(MachineAuthorization)
        .filter(
            MachineAuthorization.machine_id == machine.id,
            MachineAuthorization.user_id == body.nfc_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="User already authorized for this machine")
    auth = MachineAuthorization(
        machine_id=machine.id,
        user_id=body.nfc_id,
        price_per_login=body.price_per_login,
        price_per_minute=body.price_per_minute,
        booking_interval=body.booking_interval,
        granted_by=user_info.get("sub"),
        granted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(auth)
    db.commit()
    db.refresh(auth)
    return auth


@router.put("/{slug}/authorizations/{nfc_id}", response_model=AuthorizationResponse)
def update_authorization(
    slug: str,
    nfc_id: int,
    body: AuthorizationUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    user_info, machine = require_machine_manager(slug, request, db)
    auth = (
        db.query(MachineAuthorization)
        .filter(
            MachineAuthorization.machine_id == machine.id,
            MachineAuthorization.user_id == nfc_id,
        )
        .first()
    )
    if not auth:
        raise HTTPException(status_code=404, detail="Authorization not found")
    if body.price_per_login is not None:
        auth.price_per_login = body.price_per_login
    if body.price_per_minute is not None:
        auth.price_per_minute = body.price_per_minute
    if body.booking_interval is not None:
        auth.booking_interval = body.booking_interval
    db.commit()
    db.refresh(auth)
    return auth


@router.delete("/{slug}/authorizations/{nfc_id}", response_model=MessageResponse)
def revoke_authorization(
    slug: str,
    nfc_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user_info, machine = require_machine_manager(slug, request, db)
    auth = (
        db.query(MachineAuthorization)
        .filter(
            MachineAuthorization.machine_id == machine.id,
            MachineAuthorization.user_id == nfc_id,
        )
        .first()
    )
    if not auth:
        raise HTTPException(status_code=404, detail="Authorization not found")
    db.delete(auth)
    db.commit()
    return {"detail": "Authorization revoked"}


@router.get("/{slug}/authorize/{nfc_id}", response_model=AuthorizeUserResponse)
def check_authorization(
    slug: str,
    nfc_id: int,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """
    Device endpoint: check whether a user (by NFC ID) is authorised for this machine.

    The calling device must own the machine identified by `slug`; a device cannot
    query authorisation on a different machine.

    Returns HTTP 200 in all cases where the user exists:
    - `authorized: true`  — user is authorised; pricing fields are populated.
    - `authorized: false` — user exists but has no authorisation record for this machine.

    Returns HTTP 403 if the device token does not match the requested machine slug.
    Returns HTTP 404 if the user is not found.
    """
    if device.slug != slug:
        raise HTTPException(
            status_code=403,
            detail="Device token does not match the requested machine",
        )

    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    auth = (
        db.query(MachineAuthorization)
        .filter(
            MachineAuthorization.machine_id == device.id,
            MachineAuthorization.user_id == nfc_id,
        )
        .first()
    )

    if auth is None:
        return AuthorizeUserResponse(
            authorized=False,
            user_id=user.id,
            user_name=user.name,
            balance=user.balance,
            price_per_login=Decimal("0.00"),
            price_per_minute=Decimal("0.00"),
            booking_interval=0,
        )

    return AuthorizeUserResponse(
        authorized=True,
        user_id=user.id,
        user_name=user.name,
        balance=user.balance,
        price_per_login=auth.price_per_login,
        price_per_minute=auth.price_per_minute,
        booking_interval=auth.booking_interval,
    )
