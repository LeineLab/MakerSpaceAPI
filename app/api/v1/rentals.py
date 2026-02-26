from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device, require_admin_user
from app.database import get_db
from app.models.machine import Machine
from app.models.rental import Rental, RentalItem, RentalPermission
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.rental import (
    ActiveRentalResponse,
    RentalItemCreate,
    RentalItemResponse,
    RentalItemStatusResponse,
    RentalItemUpdate,
    RentalPermissionResponse,
    RentalResponse,
    RentRequest,
)

router = APIRouter()


# --- Rental Items ---

@router.get("/items", response_model=list[RentalItemResponse])
def list_items(
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    return db.query(RentalItem).order_by(RentalItem.name).all()


@router.post("/items", response_model=RentalItemResponse, status_code=201)
def create_item(
    body: RentalItemCreate,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    if db.query(RentalItem).filter(RentalItem.uhf_tid == body.uhf_tid.upper()).first():
        raise HTTPException(status_code=409, detail="UHF TID already registered")
    item = RentalItem(
        name=body.name,
        description=body.description,
        uhf_tid=body.uhf_tid.upper(),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/items/{item_id}", response_model=RentalItemResponse)
def update_item(
    item_id: int,
    body: RentalItemUpdate,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    item = db.query(RentalItem).filter(RentalItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if body.name is not None:
        item.name = body.name
    if body.description is not None:
        item.description = body.description
    if body.active is not None:
        item.active = body.active
    db.commit()
    db.refresh(item)
    return item


@router.get("/items/{uhf_tid}/status", response_model=RentalItemStatusResponse)
def item_status(
    uhf_tid: str,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Check if an item (by UHF TID) is currently rented."""
    item = db.query(RentalItem).filter(RentalItem.uhf_tid == uhf_tid.upper()).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    active_rental = (
        db.query(Rental)
        .filter(Rental.item_id == item.id, Rental.returned_at.is_(None))
        .first()
    )
    if active_rental:
        user = db.query(User).filter(User.id == active_rental.user_id).first()
        return RentalItemStatusResponse(
            uhf_tid=item.uhf_tid,
            item_name=item.name,
            is_rented=True,
            rental_id=active_rental.id,
            rented_by_user_id=active_rental.user_id,
            rented_by_name=user.name if user else None,
            rented_at=active_rental.rented_at,
        )
    return RentalItemStatusResponse(
        uhf_tid=item.uhf_tid,
        item_name=item.name,
        is_rented=False,
        rental_id=None,
        rented_by_user_id=None,
        rented_by_name=None,
        rented_at=None,
    )


# --- Rental operations ---

@router.get("/authorize/{nfc_id}")
def authorize_renter(
    nfc_id: int,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Check if a user has rental permission."""
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    permission = db.query(RentalPermission).filter(RentalPermission.user_id == nfc_id).first()
    return {
        "authorized": permission is not None,
        "user_id": nfc_id,
        "user_name": user.name,
    }


@router.post("", response_model=RentalResponse, status_code=201)
def rent_item(
    body: RentRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Rent an item."""
    user = db.query(User).filter(User.id == body.nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    permission = db.query(RentalPermission).filter(RentalPermission.user_id == body.nfc_id).first()
    if not permission:
        raise HTTPException(status_code=403, detail="User not authorized to rent")

    item = db.query(RentalItem).filter(RentalItem.uhf_tid == body.uhf_tid.upper()).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not item.active:
        raise HTTPException(status_code=400, detail="Item is not available for renting")

    active_rental = (
        db.query(Rental)
        .filter(Rental.item_id == item.id, Rental.returned_at.is_(None))
        .first()
    )
    if active_rental:
        raise HTTPException(status_code=409, detail="Item is already rented")

    rental = Rental(item_id=item.id, user_id=body.nfc_id, rented_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(rental)
    db.commit()
    db.refresh(rental)
    return rental


@router.delete("/{rental_id}", response_model=MessageResponse)
def return_item(
    rental_id: int,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Return a rented item."""
    rental = db.query(Rental).filter(Rental.id == rental_id).first()
    if not rental:
        raise HTTPException(status_code=404, detail="Rental not found")
    if rental.returned_at is not None:
        return {"detail": "Item was already returned"}
    rental.returned_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return {"detail": "Item returned successfully"}


@router.get("/active", response_model=list[ActiveRentalResponse])
def list_active_rentals(
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """List all currently rented items (admin only)."""
    rentals = (
        db.query(Rental)
        .filter(Rental.returned_at.is_(None))
        .order_by(Rental.rented_at)
        .all()
    )
    results = []
    for rental in rentals:
        results.append(ActiveRentalResponse(
            rental_id=rental.id,
            item_id=rental.item.id,
            item_name=rental.item.name,
            uhf_tid=rental.item.uhf_tid,
            user_id=rental.user_id,
            user_name=rental.user.name,
            rented_at=rental.rented_at,
        ))
    return results


# --- Permissions ---

@router.get("/permissions", response_model=list[RentalPermissionResponse])
def list_permissions(
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    permissions = db.query(RentalPermission).all()
    results = []
    for perm in permissions:
        results.append(RentalPermissionResponse(
            user_id=perm.user_id,
            user_name=perm.user.name,
            granted_by=perm.granted_by,
            granted_at=perm.granted_at,
        ))
    return results


@router.post("/permissions/{nfc_id}", response_model=RentalPermissionResponse, status_code=201)
def grant_permission(
    nfc_id: int,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if db.query(RentalPermission).filter(RentalPermission.user_id == nfc_id).first():
        raise HTTPException(status_code=409, detail="User already has rental permission")
    perm = RentalPermission(
        user_id=nfc_id,
        granted_by=admin.get("sub"),
        granted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return RentalPermissionResponse(
        user_id=perm.user_id,
        user_name=user.name,
        granted_by=perm.granted_by,
        granted_at=perm.granted_at,
    )


@router.delete("/permissions/{nfc_id}", response_model=MessageResponse)
def revoke_permission(
    nfc_id: int,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    perm = db.query(RentalPermission).filter(RentalPermission.user_id == nfc_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")
    db.delete(perm)
    db.commit()
    return {"detail": "Rental permission revoked"}
