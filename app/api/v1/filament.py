from datetime import UTC, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device, require_product_manager_user, require_session_user
from app.database import get_db
from app.models.filament import FilamentBrand, FilamentRoll, FilamentType
from app.models.machine import Machine
from app.schemas.common import HTTP_400, HTTP_403, HTTP_404, HTTP_409, MessageResponse
from app.schemas.filament import (
    FilamentBrandCreate,
    FilamentBrandResponse,
    FilamentManualActionRequest,
    FilamentRollCreate,
    FilamentRollResponse,
    FilamentRollUpdate,
    FilamentScanRequest,
    FilamentScanResponse,
    FilamentStatusRequest,
    FilamentStatusResponse,
    FilamentStockSummary,
    FilamentTypeCreate,
    FilamentTypeResponse,
)

router = APIRouter()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _roll_response(roll: FilamentRoll) -> FilamentRollResponse:
    return FilamentRollResponse(
        id=roll.id,
        brand_id=roll.brand_id,
        brand_name=roll.brand.name,
        type_id=roll.type_id,
        type_name=roll.type.name,
        weight_grams=roll.weight_grams,
        color=roll.color,
        vendor_serial=roll.vendor_serial,
        added_at=roll.added_at,
        added_by=roll.added_by,
        removed_at=roll.removed_at,
        removed_by=roll.removed_by,
        in_stock=roll.in_stock,
    )


def _get_or_create_brand(db: Session, name: str) -> FilamentBrand:
    name = name.strip()
    brand = db.query(FilamentBrand).filter(FilamentBrand.name == name).first()
    if not brand:
        brand = FilamentBrand(name=name)
        db.add(brand)
        db.flush()
    return brand


def _get_or_create_type(db: Session, name: str) -> FilamentType:
    name = name.strip()
    type_ = db.query(FilamentType).filter(FilamentType.name == name).first()
    if not type_:
        type_ = FilamentType(name=name)
        db.add(type_)
        db.flush()
    return type_


def _active_serial_conflict(db: Session, serial: str, exclude_roll_id: Optional[int] = None) -> bool:
    q = db.query(FilamentRoll).filter(
        FilamentRoll.vendor_serial == serial, FilamentRoll.removed_at.is_(None)
    )
    if exclude_roll_id is not None:
        q = q.filter(FilamentRoll.id != exclude_roll_id)
    return q.first() is not None


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------

@router.get("/brands", response_model=list[FilamentBrandResponse])
def list_brands(
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    return db.query(FilamentBrand).order_by(FilamentBrand.name).all()


@router.post("/brands", response_model=FilamentBrandResponse, status_code=201, responses={**HTTP_409})
def create_brand(
    body: FilamentBrandCreate,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    name = body.name.strip()
    if db.query(FilamentBrand).filter(FilamentBrand.name == name).first():
        raise HTTPException(status_code=409, detail="Brand already exists")
    brand = FilamentBrand(name=name)
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return brand


@router.delete("/brands/{brand_id}", response_model=MessageResponse, responses={**HTTP_404, **HTTP_409})
def delete_brand(
    brand_id: int,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    brand = db.query(FilamentBrand).filter(FilamentBrand.id == brand_id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    if db.query(FilamentRoll).filter(FilamentRoll.brand_id == brand_id).first():
        raise HTTPException(status_code=409, detail="Brand is in use by one or more filament rolls")
    db.delete(brand)
    db.commit()
    return {"detail": f"Brand '{brand.name}' deleted"}


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@router.get("/types", response_model=list[FilamentTypeResponse])
def list_types(
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    return db.query(FilamentType).order_by(FilamentType.name).all()


@router.post("/types", response_model=FilamentTypeResponse, status_code=201, responses={**HTTP_409})
def create_type(
    body: FilamentTypeCreate,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    name = body.name.strip()
    if db.query(FilamentType).filter(FilamentType.name == name).first():
        raise HTTPException(status_code=409, detail="Type already exists")
    type_ = FilamentType(name=name)
    db.add(type_)
    db.commit()
    db.refresh(type_)
    return type_


@router.delete("/types/{type_id}", response_model=MessageResponse, responses={**HTTP_404, **HTTP_409})
def delete_type(
    type_id: int,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    type_ = db.query(FilamentType).filter(FilamentType.id == type_id).first()
    if not type_:
        raise HTTPException(status_code=404, detail="Type not found")
    if db.query(FilamentRoll).filter(FilamentRoll.type_id == type_id).first():
        raise HTTPException(status_code=409, detail="Type is in use by one or more filament rolls")
    db.delete(type_)
    db.commit()
    return {"detail": f"Type '{type_.name}' deleted"}


# ---------------------------------------------------------------------------
# Rolls (manual, web UI — product manager / admin)
# ---------------------------------------------------------------------------

@router.get("/rolls", response_model=list[FilamentRollResponse])
def list_rolls(
    status: Literal["active", "removed", "all"] = "active",
    brand_id: Optional[int] = None,
    type_id: Optional[int] = None,
    user: dict = Depends(require_session_user),
    db: Session = Depends(get_db),
):
    query = db.query(FilamentRoll)
    if status == "active":
        query = query.filter(FilamentRoll.removed_at.is_(None))
    elif status == "removed":
        query = query.filter(FilamentRoll.removed_at.isnot(None))
    if brand_id is not None:
        query = query.filter(FilamentRoll.brand_id == brand_id)
    if type_id is not None:
        query = query.filter(FilamentRoll.type_id == type_id)
    rolls = query.order_by(FilamentRoll.added_at.desc()).all()
    return [_roll_response(r) for r in rolls]


@router.get("/rolls/summary", response_model=list[FilamentStockSummary])
def rolls_summary(
    db: Session = Depends(get_db),
):
    """Public: current in-stock count grouped by brand/type/weight/color — no
    per-roll identifying detail (vendor_serial, added_by/removed_by, timestamps)
    here, unlike GET /rolls, so this one is safe to expose with no auth at all."""
    rows = (
        db.query(
            FilamentRoll.brand_id,
            FilamentRoll.type_id,
            FilamentRoll.weight_grams,
            FilamentRoll.color,
            func.count(FilamentRoll.id),
        )
        .filter(FilamentRoll.removed_at.is_(None))
        .group_by(FilamentRoll.brand_id, FilamentRoll.type_id, FilamentRoll.weight_grams, FilamentRoll.color)
        .all()
    )
    brands = {b.id: b.name for b in db.query(FilamentBrand).all()}
    types = {t.id: t.name for t in db.query(FilamentType).all()}
    result = [
        FilamentStockSummary(
            brand_id=brand_id,
            brand_name=brands.get(brand_id, "?"),
            type_id=type_id,
            type_name=types.get(type_id, "?"),
            weight_grams=weight_grams,
            color=color,
            count_in_stock=count,
        )
        for brand_id, type_id, weight_grams, color, count in rows
    ]
    result.sort(key=lambda s: (s.brand_name, s.type_name, s.color, s.weight_grams))
    return result


@router.post("/rolls", response_model=list[FilamentRollResponse], status_code=201, responses={**HTTP_400, **HTTP_404, **HTTP_409})
def create_rolls(
    body: FilamentRollCreate,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    if not db.query(FilamentBrand).filter(FilamentBrand.id == body.brand_id).first():
        raise HTTPException(status_code=404, detail="Brand not found")
    if not db.query(FilamentType).filter(FilamentType.id == body.type_id).first():
        raise HTTPException(status_code=404, detail="Type not found")

    serial = body.vendor_serial.strip() if body.vendor_serial else None
    if serial and body.quantity > 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot add more than one roll at once with the same serial number",
        )
    if serial and _active_serial_conflict(db, serial):
        raise HTTPException(status_code=409, detail="A roll with this serial is already in stock")

    color = body.color.strip()
    created = []
    for _ in range(body.quantity):
        roll = FilamentRoll(
            brand_id=body.brand_id,
            type_id=body.type_id,
            weight_grams=body.weight_grams,
            color=color,
            vendor_serial=serial,
            added_at=_now(),
            added_by=user.get("sub"),
        )
        db.add(roll)
        created.append(roll)
    db.commit()
    for r in created:
        db.refresh(r)
    return [_roll_response(r) for r in created]


@router.put("/rolls/{roll_id}", response_model=FilamentRollResponse, responses={**HTTP_400, **HTTP_404, **HTTP_409})
def update_roll(
    roll_id: int,
    body: FilamentRollUpdate,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    roll = db.query(FilamentRoll).filter(FilamentRoll.id == roll_id).first()
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    if body.brand_id is not None:
        if not db.query(FilamentBrand).filter(FilamentBrand.id == body.brand_id).first():
            raise HTTPException(status_code=404, detail="Brand not found")
        roll.brand_id = body.brand_id
    if body.type_id is not None:
        if not db.query(FilamentType).filter(FilamentType.id == body.type_id).first():
            raise HTTPException(status_code=404, detail="Type not found")
        roll.type_id = body.type_id
    if body.weight_grams is not None:
        roll.weight_grams = body.weight_grams
    if body.color is not None:
        roll.color = body.color.strip()
    if "vendor_serial" in body.model_fields_set:
        new_serial = body.vendor_serial.strip() if body.vendor_serial else None
        if new_serial and roll.in_stock and _active_serial_conflict(db, new_serial, exclude_roll_id=roll.id):
            raise HTTPException(status_code=409, detail="A roll with this serial is already in stock")
        roll.vendor_serial = new_serial
    db.commit()
    db.refresh(roll)
    return _roll_response(roll)


@router.post("/rolls/{roll_id}/remove", response_model=FilamentRollResponse, responses={**HTTP_404, **HTTP_409})
def remove_roll(
    roll_id: int,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    """Manual Ausbuchen."""
    roll = db.query(FilamentRoll).filter(FilamentRoll.id == roll_id).first()
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    if not roll.in_stock:
        raise HTTPException(status_code=409, detail="Roll is already removed")
    roll.removed_at = _now()
    roll.removed_by = user.get("sub")
    db.commit()
    db.refresh(roll)
    return _roll_response(roll)


@router.post("/rolls/{roll_id}/restore", response_model=FilamentRollResponse, responses={**HTTP_400, **HTTP_404})
def restore_roll(
    roll_id: int,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    """Undo an accidental Ausbuchen."""
    roll = db.query(FilamentRoll).filter(FilamentRoll.id == roll_id).first()
    if not roll:
        raise HTTPException(status_code=404, detail="Roll not found")
    if roll.in_stock:
        raise HTTPException(status_code=400, detail="Roll is not currently removed")
    if roll.vendor_serial and _active_serial_conflict(db, roll.vendor_serial, exclude_roll_id=roll.id):
        raise HTTPException(
            status_code=409,
            detail="Another roll with this serial is already in stock",
        )
    roll.removed_at = None
    roll.removed_by = None
    db.commit()
    db.refresh(roll)
    return _roll_response(roll)


# ---------------------------------------------------------------------------
# Device endpoints (ESP32 filament station — Bearer machine token)
# ---------------------------------------------------------------------------

@router.post("/scan", response_model=FilamentScanResponse)
def scan_tag(
    body: FilamentScanRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Deterministic check-in/check-out for tag formats that carry a
    manufacturer serial. Without a serial (OpenSpool), no action is taken —
    the caller must show a manual choice and call /checkin or /checkout."""
    weight = body.weight_grams
    color = body.color.strip()
    serial = body.vendor_serial.strip() if body.vendor_serial else None

    if not serial:
        brand = db.query(FilamentBrand).filter(FilamentBrand.name == body.brand_name.strip()).first()
        type_ = db.query(FilamentType).filter(FilamentType.name == body.type_name.strip()).first()
        current_stock = 0
        if brand and type_:
            current_stock = (
                db.query(FilamentRoll)
                .filter(
                    FilamentRoll.brand_id == brand.id,
                    FilamentRoll.type_id == type_.id,
                    FilamentRoll.weight_grams == weight,
                    FilamentRoll.color == color,
                    FilamentRoll.removed_at.is_(None),
                )
                .count()
            )
        return FilamentScanResponse(identify_required=True, current_stock=current_stock)

    existing = db.query(FilamentRoll).filter(
        FilamentRoll.vendor_serial == serial, FilamentRoll.removed_at.is_(None)
    ).first()
    if existing:
        existing.removed_at = _now()
        existing.removed_by = f"device:{device.slug}"
        db.commit()
        db.refresh(existing)
        return FilamentScanResponse(identify_required=False, action="checked_out", roll=_roll_response(existing))

    brand = _get_or_create_brand(db, body.brand_name)
    type_ = _get_or_create_type(db, body.type_name)
    roll = FilamentRoll(
        brand_id=brand.id,
        type_id=type_.id,
        weight_grams=weight,
        color=color,
        vendor_serial=serial,
        added_at=_now(),
        added_by=f"device:{device.slug}",
    )
    db.add(roll)
    db.commit()
    db.refresh(roll)
    return FilamentScanResponse(identify_required=False, action="checked_in", roll=_roll_response(roll))


@router.post("/status", response_model=FilamentStatusResponse)
def device_status(
    body: FilamentStatusRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Read-only: reports whether a roll with this serial is currently in
    stock, without checking anything in or out. Lets the station show an
    explicit Einbuchen/Ausbuchen confirmation before acting, instead of
    /scan's deterministic auto-action — scanning the same physical spool
    twice in a row would otherwise silently flip its state with no chance
    for the operator to notice or stop it."""
    serial = body.vendor_serial.strip()
    roll = db.query(FilamentRoll).filter(
        FilamentRoll.vendor_serial == serial, FilamentRoll.removed_at.is_(None)
    ).first()
    return FilamentStatusResponse(in_stock=roll is not None, roll=_roll_response(roll) if roll else None)


@router.post("/checkin", response_model=FilamentRollResponse, responses={**HTTP_409})
def device_checkin(
    body: FilamentManualActionRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Explicit Einbuchen — used for the OpenSpool no-serial flow after the
    station's user picked "check in", or for a serialized tag directly."""
    serial = body.vendor_serial.strip() if body.vendor_serial else None
    if serial and _active_serial_conflict(db, serial):
        raise HTTPException(status_code=409, detail="A roll with this serial is already in stock")

    brand = _get_or_create_brand(db, body.brand_name)
    type_ = _get_or_create_type(db, body.type_name)
    roll = FilamentRoll(
        brand_id=brand.id,
        type_id=type_.id,
        weight_grams=body.weight_grams,
        color=body.color.strip(),
        vendor_serial=serial,
        added_at=_now(),
        added_by=f"device:{device.slug}",
    )
    db.add(roll)
    db.commit()
    db.refresh(roll)
    return _roll_response(roll)


@router.post("/checkout", response_model=FilamentRollResponse, responses={**HTTP_404})
def device_checkout(
    body: FilamentManualActionRequest,
    device: Machine = Depends(get_current_device),
    db: Session = Depends(get_db),
):
    """Explicit Ausbuchen. With a serial, matches that exact roll. Without one
    (OpenSpool), removes the oldest in-stock roll matching the given spec
    (FIFO) — there's no identity to pick a specific physical instance by."""
    serial = body.vendor_serial.strip() if body.vendor_serial else None
    query = db.query(FilamentRoll).filter(FilamentRoll.removed_at.is_(None))

    if serial:
        roll = query.filter(FilamentRoll.vendor_serial == serial).first()
        if not roll:
            raise HTTPException(status_code=404, detail="No roll with this serial is currently in stock")
    else:
        brand = db.query(FilamentBrand).filter(FilamentBrand.name == body.brand_name.strip()).first()
        type_ = db.query(FilamentType).filter(FilamentType.name == body.type_name.strip()).first()
        if not brand or not type_:
            raise HTTPException(status_code=404, detail="Unknown brand or type")
        roll = (
            query.filter(
                FilamentRoll.brand_id == brand.id,
                FilamentRoll.type_id == type_.id,
                FilamentRoll.weight_grams == body.weight_grams,
                FilamentRoll.color == body.color.strip(),
                FilamentRoll.vendor_serial.is_(None),
            )
            .order_by(FilamentRoll.added_at.asc())
            .first()
        )
        if not roll:
            raise HTTPException(status_code=404, detail="No matching roll currently in stock")

    roll.removed_at = _now()
    roll.removed_by = f"device:{device.slug}"
    db.commit()
    db.refresh(roll)
    return _roll_response(roll)
