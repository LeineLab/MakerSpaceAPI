import re
from datetime import UTC, date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import require_session_user
from app.config import settings
from app.database import get_db
from app.models.inventory import InventoryBatch, InventoryItem, InventoryLocation
from app.schemas.common import HTTP_400, HTTP_404, HTTP_409, MessageResponse
from app.schemas.inventory import (
    InventoryBatchCreate,
    InventoryBatchResponse,
    InventoryBatchUpdate,
    InventoryItemCreate,
    InventoryItemResponse,
    InventoryItemUpdate,
    InventoryLocationCreate,
    InventoryLocationResponse,
    InventoryLocationUpdate,
    InventoryQuantityUpdate,
    InventoryShoppingRow,
    InventorySummary,
)
from app.services.audit import audit, changes

# Every logged-in user may read and write: keeping counts current is meant to
# be a quick job for whoever just used something up, so no role gate here. Each
# change is recorded in the audit log under the acting user instead.
router = APIRouter(dependencies=[Depends(require_session_user)])

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _today() -> date:
    return datetime.now(UTC).date()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().translate(_UMLAUTS)).strip("-")
    return s[:90] or "standort"


def _unique_slug(db: Session, name: str) -> str:
    base = _slugify(name)
    slug, n = base, 2
    while db.query(InventoryLocation).filter(InventoryLocation.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _batch_state(expires_on: Optional[date], today: date, warn_until: date) -> str:
    if expires_on is None:
        return "ok"
    if expires_on < today:
        return "expired"
    if expires_on <= warn_until:
        return "expiring"
    return "ok"


def _item_response(item: InventoryItem) -> InventoryItemResponse:
    today = _today()
    warn_until = today + timedelta(days=settings.INVENTORY_EXPIRY_WARN_DAYS)
    batches = [
        InventoryBatchResponse(
            id=b.id, quantity=b.quantity, expires_on=b.expires_on,
            state=_batch_state(b.expires_on, today, warn_until),
        )
        for b in item.batches
    ] if item.track_expiry else []
    on_hand = item.on_hand
    expired = sum(b.quantity for b in batches if b.state == "expired")
    expiring = sum(b.quantity for b in batches if b.state == "expiring")
    usable = on_hand - expired
    return InventoryItemResponse(
        id=item.id,
        location_id=item.location_id,
        name=item.name,
        unit=item.unit,
        target_qty=item.target_qty,
        track_expiry=item.track_expiry,
        notes=item.notes,
        on_hand=on_hand,
        usable_qty=usable,
        expired_qty=expired,
        expiring_soon_qty=expiring,
        to_order=max(0, item.target_qty - (usable - expiring)),
        batches=batches,
    )


def _snapshot(item: InventoryItem) -> dict:
    return {
        "location_id": item.location_id, "name": item.name, "unit": item.unit,
        "target_qty": item.target_qty, "track_expiry": item.track_expiry, "notes": item.notes,
    }


def _needs_attention(r: InventoryItemResponse) -> bool:
    return r.to_order > 0 or r.expired_qty > 0


def _get_location(db: Session, location_id: int) -> InventoryLocation:
    loc = db.get(InventoryLocation, location_id)
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    return loc


def _get_item(db: Session, item_id: int) -> InventoryItem:
    item = db.get(InventoryItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


def _get_batch(db: Session, batch_id: int) -> InventoryBatch:
    batch = db.get(InventoryBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


def _ensure_unique_item_name(db: Session, location_id: int, name: str, exclude_id: Optional[int] = None) -> None:
    q = db.query(InventoryItem).filter(InventoryItem.location_id == location_id, InventoryItem.name == name)
    if exclude_id is not None:
        q = q.filter(InventoryItem.id != exclude_id)
    if q.first():
        raise HTTPException(status_code=409, detail="An item with this name already exists at this location")


def _add_to_batch(item: InventoryItem, quantity: int, expires_on: Optional[date]) -> None:
    """Adds to the batch with the same date if there is one, so counting the
    same delivery twice never produces two rows for one best-before date."""
    for b in item.batches:
        if b.expires_on == expires_on:
            b.quantity += quantity
            return
    item.batches.append(InventoryBatch(quantity=quantity, expires_on=expires_on))


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

@router.get("/locations", response_model=list[InventoryLocationResponse])
def list_locations(db: Session = Depends(get_db)):
    return db.query(InventoryLocation).order_by(InventoryLocation.name).all()


@router.post("/locations", response_model=InventoryLocationResponse, status_code=201, responses={**HTTP_409})
def create_location(body: InventoryLocationCreate, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.query(InventoryLocation).filter(InventoryLocation.name == name).first():
        raise HTTPException(status_code=409, detail="A location with this name already exists")
    loc = InventoryLocation(name=name, slug=_unique_slug(db, name), notes=body.notes)
    db.add(loc)
    db.flush()
    audit(db, user.get("sub"), "inventory.location.created", "inventory_location", loc.id, name=name)
    db.commit()
    db.refresh(loc)
    return loc


@router.put("/locations/{location_id}", response_model=InventoryLocationResponse, responses={**HTTP_404, **HTTP_409})
def update_location(location_id: int, body: InventoryLocationUpdate, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    loc = _get_location(db, location_id)
    before = {"name": loc.name, "notes": loc.notes}
    if body.name is not None:
        name = body.name.strip()
        if db.query(InventoryLocation).filter(InventoryLocation.name == name, InventoryLocation.id != loc.id).first():
            raise HTTPException(status_code=409, detail="A location with this name already exists")
        loc.name = name  # slug stays, QR links must not break on rename
    if body.notes is not None:
        loc.notes = body.notes or None
    diff = changes(before, {"name": loc.name, "notes": loc.notes})
    if diff:
        audit(db, user.get("sub"), "inventory.location.updated", "inventory_location", loc.id, **diff)
    db.commit()
    db.refresh(loc)
    return loc


@router.delete("/locations/{location_id}", response_model=MessageResponse, responses={**HTTP_404, **HTTP_409})
def delete_location(location_id: int, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    loc = _get_location(db, location_id)
    if loc.items:
        raise HTTPException(status_code=409, detail="Location still has items; remove or move them first")
    audit(db, user.get("sub"), "inventory.location.deleted", "inventory_location", loc.id, name=loc.name)
    db.delete(loc)
    db.commit()
    return MessageResponse(detail="Location deleted")


@router.post("/locations/{location_id}/check", response_model=InventoryLocationResponse, responses={**HTTP_404})
def mark_location_checked(location_id: int, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    """Record that someone just went through this location and verified it."""
    loc = _get_location(db, location_id)
    loc.last_checked_at = _now()
    loc.last_checked_by = user.get("sub")
    audit(db, user.get("sub"), "inventory.location.checked", "inventory_location", loc.id, name=loc.name)
    db.commit()
    db.refresh(loc)
    return loc


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

@router.get("/items", response_model=list[InventoryItemResponse])
def list_items(location_id: Optional[int] = Query(default=None), db: Session = Depends(get_db)):
    q = db.query(InventoryItem)
    if location_id is not None:
        q = q.filter(InventoryItem.location_id == location_id)
    return [_item_response(i) for i in q.order_by(InventoryItem.name).all()]


@router.post("/items", response_model=InventoryItemResponse, status_code=201, responses={**HTTP_404, **HTTP_409})
def create_item(body: InventoryItemCreate, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    _get_location(db, body.location_id)
    name = body.name.strip()
    _ensure_unique_item_name(db, body.location_id, name)
    item = InventoryItem(
        location_id=body.location_id, name=name, unit=body.unit or None,
        target_qty=body.target_qty, track_expiry=body.track_expiry, notes=body.notes or None,
    )
    if body.track_expiry:
        if body.quantity > 0:
            item.batches.append(InventoryBatch(quantity=body.quantity, expires_on=body.expires_on))
    else:
        item.quantity = body.quantity
    db.add(item)
    db.flush()
    audit(db, user.get("sub"), "inventory.item.created", "inventory_item", item.id,
          **_snapshot(item), quantity=item.on_hand)
    db.commit()
    db.refresh(item)
    return _item_response(item)


@router.put("/items/{item_id}", response_model=InventoryItemResponse, responses={**HTTP_404, **HTTP_409})
def update_item(item_id: int, body: InventoryItemUpdate, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    before = _snapshot(item)
    if body.location_id is not None and body.location_id != item.location_id:
        _get_location(db, body.location_id)
        item.location_id = body.location_id
    if body.name is not None:
        item.name = body.name.strip()
    if body.unit is not None:
        item.unit = body.unit or None
    if body.target_qty is not None:
        item.target_qty = body.target_qty
    if body.notes is not None:
        item.notes = body.notes or None
    _ensure_unique_item_name(db, item.location_id, item.name, exclude_id=item.id)
    if body.track_expiry is not None and body.track_expiry != item.track_expiry:
        if body.track_expiry:
            if item.quantity > 0:
                item.batches.append(InventoryBatch(quantity=item.quantity, expires_on=None))
            item.quantity = 0
        else:
            item.quantity = sum(b.quantity for b in item.batches)
            item.batches.clear()
        item.track_expiry = body.track_expiry
    diff = changes(before, _snapshot(item))
    if diff:
        audit(db, user.get("sub"), "inventory.item.updated", "inventory_item", item.id, **diff)
    db.commit()
    db.refresh(item)
    return _item_response(item)


@router.delete("/items/{item_id}", response_model=MessageResponse, responses={**HTTP_404})
def delete_item(item_id: int, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    audit(db, user.get("sub"), "inventory.item.deleted", "inventory_item", item.id,
          **_snapshot(item), quantity=item.on_hand)
    db.delete(item)
    db.commit()
    return MessageResponse(detail="Item deleted")


@router.post("/items/{item_id}/quantity", response_model=InventoryItemResponse, responses={**HTTP_400, **HTTP_404})
def set_item_quantity(item_id: int, body: InventoryQuantityUpdate, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    """Count of an item without expiry tracking. Items with expiry tracking
    change through their batches instead."""
    item = _get_item(db, item_id)
    if item.track_expiry:
        raise HTTPException(status_code=400, detail="Item tracks expiry; change its batches instead")
    new = body.quantity if body.quantity is not None else item.quantity + body.delta
    if new < 0:
        raise HTTPException(status_code=400, detail="Quantity cannot go below zero")
    if new != item.quantity:
        audit(db, user.get("sub"), "inventory.item.quantity", "inventory_item", item.id,
              name=item.name, quantity={"from": item.quantity, "to": new})
        item.quantity = new
    db.commit()
    db.refresh(item)
    return _item_response(item)


# ---------------------------------------------------------------------------
# Batches (only for items with expiry tracking)
# ---------------------------------------------------------------------------

@router.post("/items/{item_id}/batches", response_model=InventoryItemResponse, status_code=201, responses={**HTTP_400, **HTTP_404})
def add_batch(item_id: int, body: InventoryBatchCreate, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    item = _get_item(db, item_id)
    if not item.track_expiry:
        raise HTTPException(status_code=400, detail="Item does not track expiry")
    _add_to_batch(item, body.quantity, body.expires_on)
    audit(db, user.get("sub"), "inventory.batch.added", "inventory_item", item.id,
          name=item.name, quantity=body.quantity, expires_on=body.expires_on)
    db.commit()
    db.refresh(item)
    return _item_response(item)


@router.put("/batches/{batch_id}", response_model=InventoryItemResponse, responses={**HTTP_404, **HTTP_409})
def update_batch(batch_id: int, body: InventoryBatchUpdate, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    """Quantity 0 removes the batch (the last of it was used)."""
    batch = _get_batch(db, batch_id)
    item = batch.item
    before = {"quantity": batch.quantity, "expires_on": batch.expires_on}
    if body.quantity == 0:
        audit(db, user.get("sub"), "inventory.batch.removed", "inventory_item", item.id,
              name=item.name, **before)
        item.batches.remove(batch)
    else:
        if body.quantity is not None:
            batch.quantity = body.quantity
        if body.clear_expires_on:
            batch.expires_on = None
        elif body.expires_on is not None:
            batch.expires_on = body.expires_on
        if any(b.id != batch.id and b.expires_on == batch.expires_on for b in item.batches):
            raise HTTPException(status_code=409, detail="A batch with this date already exists; change its quantity instead")
        diff = changes(before, {"quantity": batch.quantity, "expires_on": batch.expires_on})
        if diff:
            audit(db, user.get("sub"), "inventory.batch.updated", "inventory_item", item.id,
                  name=item.name, **diff)
    db.commit()
    db.refresh(item)
    return _item_response(item)


@router.delete("/batches/{batch_id}", response_model=InventoryItemResponse, responses={**HTTP_404})
def delete_batch(batch_id: int, user: dict = Depends(require_session_user), db: Session = Depends(get_db)):
    batch = _get_batch(db, batch_id)
    item = batch.item
    audit(db, user.get("sub"), "inventory.batch.removed", "inventory_item", item.id,
          name=item.name, quantity=batch.quantity, expires_on=batch.expires_on)
    item.batches.remove(batch)
    db.commit()
    db.refresh(item)
    return _item_response(item)


# ---------------------------------------------------------------------------
# Shopping list / badge
# ---------------------------------------------------------------------------

def _attention_rows(db: Session) -> list[InventoryShoppingRow]:
    rows = []
    for item in db.query(InventoryItem).all():
        r = _item_response(item)
        if _needs_attention(r):
            rows.append(InventoryShoppingRow(
                item=r, location_name=item.location.name, location_slug=item.location.slug,
            ))
    rows.sort(key=lambda x: (x.location_name.lower(), x.item.name.lower()))
    return rows


@router.get("/shopping-list", response_model=list[InventoryShoppingRow])
def shopping_list(db: Session = Depends(get_db)):
    """Items below target, or holding expired stock. Items whose stock is only
    about to expire count as short too, so their replacement is bought in time."""
    return _attention_rows(db)


@router.get("/summary", response_model=InventorySummary)
def summary(db: Session = Depends(get_db)):
    return InventorySummary(attention_items=len(_attention_rows(db)))
