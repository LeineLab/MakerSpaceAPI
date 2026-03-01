import io
import json
import zipfile
from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_device, require_admin_user
from app.config import settings
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

_TYP_PATH = Path(__file__).parent.parent.parent.parent / "statements" / "uni.typ"

_STATEMENT_TYPES = [
    TransactionType.topup,
    TransactionType.booking_target_topup,
    TransactionType.booking_target_payout,
]


def _statement_labels(lang: str) -> dict:
    if lang == "de":
        return {
            "period_prefix": "Vom",
            "period_connector": "bis",
            "balance_old": "Alter Barbestand zum Tagesbeginn des",
            "balance_new": "Neuer Barbestand am Tagesende des",
            "sum_inflows": "Summe Einzahlungen im Zeitraum",
            "sum_outflows": "Summe Entnahmen im Zeitraum",
            "section_summary": "Barbestand und Aktivitäten",
            "section_transactions": "Einzahlungen und Entnahmen",
            "col_timestamp": "Zeitstempel",
            "col_description": "Beschreibung",
            "col_amount": "Bestandsänderung",
            "generated": "Dieser Auszug wurde generiert am",
            "title_all": "Gesamtübersicht Bankomat",
        }
    return {
        "period_prefix": "From",
        "period_connector": "to",
        "balance_old": "Opening balance at start of",
        "balance_new": "Closing balance at end of",
        "sum_inflows": "Total inflows in period",
        "sum_outflows": "Total outflows in period",
        "section_summary": "Balance and Activity",
        "section_transactions": "Inflows and Outflows",
        "col_timestamp": "Timestamp",
        "col_description": "Description",
        "col_amount": "Amount",
        "generated": "This statement was generated on",
        "title_all": "Combined Bankomat Statement",
    }


def _compile_month_pdf(target: BookingTarget, year: int, month: int, lang: str, db: Session) -> bytes:
    import typst  # optional dependency

    period_start = date(year, month, 1)
    last_day = monthrange(year, month)[1]
    period_end = date(year, month, last_day)
    ds = datetime(year, month, 1)
    de = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

    base_filter = [
        Transaction.target_id == target.id,
        Transaction.type.in_(_STATEMENT_TYPES),
    ]

    def _sum_amount(*extra):
        result = db.query(func.sum(Transaction.amount)).filter(*base_filter, *extra).scalar()
        return Decimal(result or 0)

    balance_old = _sum_amount(Transaction.created_at < ds)
    balance_new = _sum_amount(Transaction.created_at < de)
    sum_inflows = _sum_amount(Transaction.created_at >= ds, Transaction.created_at < de, Transaction.amount > 0)
    sum_outflows = _sum_amount(Transaction.created_at >= ds, Transaction.created_at < de, Transaction.amount < 0)

    txs = (
        db.query(Transaction)
        .filter(*base_filter, Transaction.created_at >= ds, Transaction.created_at < de)
        .order_by(Transaction.created_at)
        .all()
    )

    items = [
        {
            "timestamp": tx.created_at.strftime("%d.%m.%Y %H:%M"),
            "description": tx.note or "",
            "line_amount": f"{tx.amount:.2f} {settings.CURRENCY}",
        }
        for tx in txs
    ]

    data = {
        "title": target.name,
        "period_start": period_start.strftime("%d.%m.%Y"),
        "period_end": period_end.strftime("%d.%m.%Y"),
        "balance_old": f"{balance_old:.2f} {settings.CURRENCY}",
        "balance_new": f"{balance_new:.2f} {settings.CURRENCY}",
        "sum_inflows": f"{sum_inflows:.2f} {settings.CURRENCY}",
        "sum_outflows": f"{sum_outflows:.2f} {settings.CURRENCY}",
        "items": items,
        "labels": _statement_labels(lang),
    }

    font_paths = [settings.TYPST_FONT_DIR] if settings.TYPST_FONT_DIR else []
    return typst.compile(
        input=str(_TYP_PATH),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        font_paths=font_paths,
    )

def _compile_month_pdf_all(year: int, month: int, lang: str, db: Session) -> bytes:
    import typst  # optional dependency

    period_start = date(year, month, 1)
    last_day = monthrange(year, month)[1]
    period_end = date(year, month, last_day)
    ds = datetime(year, month, 1)
    de = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

    base_filter = [Transaction.type.in_(_STATEMENT_TYPES)]

    def _sum_amount(*extra):
        result = db.query(func.sum(Transaction.amount)).filter(*base_filter, *extra).scalar()
        return Decimal(result or 0)

    balance_old = _sum_amount(Transaction.created_at < ds)
    balance_new = _sum_amount(Transaction.created_at < de)
    sum_inflows = _sum_amount(Transaction.created_at >= ds, Transaction.created_at < de, Transaction.amount > 0)
    sum_outflows = _sum_amount(Transaction.created_at >= ds, Transaction.created_at < de, Transaction.amount < 0)

    txs = (
        db.query(Transaction)
        .filter(*base_filter, Transaction.created_at >= ds, Transaction.created_at < de)
        .order_by(Transaction.created_at)
        .all()
    )

    # Pre-load target names to avoid N+1 queries
    target_ids = {tx.target_id for tx in txs if tx.target_id is not None}
    target_names: dict[int, str] = {}
    if target_ids:
        for t in db.query(BookingTarget).filter(BookingTarget.id.in_(target_ids)).all():
            target_names[t.id] = t.name

    items = []
    for tx in txs:
        target_name = target_names.get(tx.target_id, "") if tx.target_id else ""
        if target_name and tx.note:
            description = f"{target_name}: {tx.note}"
        elif target_name:
            description = target_name
        else:
            description = tx.note or ""
        items.append({
            "timestamp": tx.created_at.strftime("%d.%m.%Y %H:%M"),
            "description": description,
            "line_amount": f"{tx.amount:.2f} {settings.CURRENCY}",
        })

    labels = _statement_labels(lang)
    data = {
        "title": labels["title_all"],
        "period_start": period_start.strftime("%d.%m.%Y"),
        "period_end": period_end.strftime("%d.%m.%Y"),
        "balance_old": f"{balance_old:.2f} {settings.CURRENCY}",
        "balance_new": f"{balance_new:.2f} {settings.CURRENCY}",
        "sum_inflows": f"{sum_inflows:.2f} {settings.CURRENCY}",
        "sum_outflows": f"{sum_outflows:.2f} {settings.CURRENCY}",
        "items": items,
        "labels": labels,
    }

    font_paths = [settings.TYPST_FONT_DIR] if settings.TYPST_FONT_DIR else []
    return typst.compile(
        input=str(_TYP_PATH),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        font_paths=font_paths,
    )


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
    return {"detail": f"Topped up {body.amount} {settings.CURRENCY}. New balance: {user.balance} {settings.CURRENCY}", "balance": user.balance}


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
    return {"detail": f"Target '{target.name}' balance increased by {body.amount} {settings.CURRENCY}"}


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
    return {"detail": f"Transferred {body.amount} {settings.CURRENCY} from {body.from_nfc_id} to {body.to_nfc_id}"}


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
    return {"detail": f"Payout of {body.amount} {settings.CURRENCY} from '{target.name}' successful"}


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


@router.get("/statement/{target_slug}")
def get_statement(
    target_slug: str,
    from_year: int = Query(...),
    from_month: int = Query(...),
    to_year: int = Query(...),
    to_month: int = Query(...),
    lang: str = Query(default="de"),
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Generate a PDF (single month) or ZIP of PDFs (multi-month) for a booking target."""
    today = date.today()
    current_month_start = date(today.year, today.month, 1)

    if not (1 <= from_month <= 12 and 1 <= to_month <= 12):
        raise HTTPException(400, "Month must be between 1 and 12")

    from_date = date(from_year, from_month, 1)
    to_date = date(to_year, to_month, 1)

    if from_date > to_date:
        raise HTTPException(400, "Start must not be after end")
    if to_date >= current_month_start:
        raise HTTPException(400, "End month must be fully in the past")

    target = db.query(BookingTarget).filter(BookingTarget.slug == target_slug).first()
    if not target:
        raise HTTPException(404, f"Booking target '{target_slug}' not found")

    # Build list of (year, month) tuples in range
    months = []
    y, m = from_year, from_month
    while (y, m) <= (to_year, to_month):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1

    try:
        if len(months) == 1:
            y, m = months[0]
            pdf = _compile_month_pdf(target, y, m, lang, db)
            filename = f"statement_{target_slug}_{y}_{m:02d}.pdf"
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for y, m in months:
                pdf = _compile_month_pdf(target, y, m, lang, db)
                zf.writestr(f"statement_{target_slug}_{y}_{m:02d}.pdf", pdf)
        filename = f"statements_{target_slug}_{from_year}_{from_month:02d}_to_{to_year}_{to_month:02d}.zip"
        return Response(
            content=zip_buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(500, f"PDF generation failed: {e}") from e


@router.get("/statement-all")
def get_statement_all(
    from_year: int = Query(...),
    from_month: int = Query(...),
    to_year: int = Query(...),
    to_month: int = Query(...),
    lang: str = Query(default="de"),
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Generate a combined PDF (or ZIP) for all booking targets."""
    today = date.today()
    current_month_start = date(today.year, today.month, 1)

    if not (1 <= from_month <= 12 and 1 <= to_month <= 12):
        raise HTTPException(400, "Month must be between 1 and 12")

    from_date = date(from_year, from_month, 1)
    to_date = date(to_year, to_month, 1)

    if from_date > to_date:
        raise HTTPException(400, "Start must not be after end")
    if to_date >= current_month_start:
        raise HTTPException(400, "End month must be fully in the past")

    months = []
    y, m = from_year, from_month
    while (y, m) <= (to_year, to_month):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1

    try:
        if len(months) == 1:
            y, m = months[0]
            pdf = _compile_month_pdf_all(y, m, lang, db)
            filename = f"statement_all_{y}_{m:02d}.pdf"
            return Response(
                content=pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for y, m in months:
                pdf = _compile_month_pdf_all(y, m, lang, db)
                zf.writestr(f"statement_all_{y}_{m:02d}.pdf", pdf)
        filename = f"statements_all_{from_year}_{from_month:02d}_to_{to_year}_{to_month:02d}.zip"
        return Response(
            content=zip_buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(500, f"PDF generation failed: {e}") from e


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
