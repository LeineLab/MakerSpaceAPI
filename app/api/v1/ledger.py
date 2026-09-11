import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.auth.deps import require_ledger_viewer_user, require_treasurer_user
from app.config import settings
from app.database import get_db
from app.models.booking_target import BookingTarget
from app.models.ledger import (
    BankAccount,
    FintsBankPreset,
    LedgerCategory,
    LedgerCategoryKind,
    LedgerEntry,
    LedgerEntryLine,
    LedgerImportBatch,
    LedgerImportLine,
    LedgerImportSource,
    LedgerImportStatus,
)
from app.models.transaction import Transaction, TransactionType
from app.schemas.booking_target import BookingTargetResponse
from app.schemas.common import HTTP_400, HTTP_404, HTTP_409, HTTP_502, MessageResponse
from app.schemas.ledger import (
    BankAccountBalanceResponse,
    BankAccountCreate,
    BankAccountResponse,
    BankAccountUpdate,
    BookTargetPayoutRequest,
    CsvPreviewResponse,
    EuerCategoryTotal,
    EuerReportResponse,
    FinTSImportRequest,
    FinTSPollRequest,
    FinTSStartRequest,
    FinTSTanRequest,
    FintsBankPresetCreate,
    FintsBankPresetResponse,
    LedgerCategoryCreate,
    LedgerCategoryResponse,
    LedgerCategoryUpdate,
    LedgerConfigResponse,
    LedgerEntryCreate,
    LedgerEntryResponse,
    LedgerImportLineBookRequest,
    LedgerImportLineResponse,
    LedgerImportSummary,
    LedgerTargetPayoutResponse,
    LedgerTargetUpdate,
    PaperlessDocumentResult,
)
from app.services import fints_client, paperless
from app.services.bank_statement import (
    CsvColumnMapping,
    UnsupportedStatementFormat,
    account_identifier_matches,
    guess_csv_mapping,
    parse_csv_statement,
    parse_statement_file,
    preview_csv,
)
from app.web.i18n import get_translator

router = APIRouter()


@router.get("/config", response_model=LedgerConfigResponse)
def get_config(_viewer: dict = Depends(require_ledger_viewer_user)):
    """Frontend-facing feature flags (e.g. whether to show the Sphäre field)."""
    return LedgerConfigResponse(
        spheres_enabled=settings.LEDGER_SPHERES_ENABLED,
        paperless_enabled=paperless.is_configured(),
        paperless_url=settings.PAPERLESS_URL if paperless.is_configured() else None,
        fints_enabled=bool(settings.FINTS_PRODUCT_ID),
    )


# --- Bank accounts ---

@router.get("/accounts", response_model=list[BankAccountResponse])
def list_accounts(
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    return db.query(BankAccount).order_by(BankAccount.name).all()


@router.post("/accounts", response_model=BankAccountResponse, status_code=201, responses={**HTTP_400, **HTTP_409})
def create_account(
    body: BankAccountCreate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    if body.is_offline:
        if body.iban:
            raise HTTPException(status_code=400, detail="Offline accounts must not have an IBAN")
    elif not body.iban:
        raise HTTPException(status_code=400, detail="IBAN is required unless the account is offline")

    if body.iban and db.query(BankAccount).filter(BankAccount.iban == body.iban).first():
        raise HTTPException(status_code=409, detail="IBAN already registered")
    account = BankAccount(
        iban=body.iban if not body.is_offline else None,
        name=body.name,
        is_offline=body.is_offline,
        opening_balance=body.opening_balance,
        tracked=body.tracked,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _computed_balance(db: Session, account: BankAccount, as_of: date) -> Decimal:
    booked_sum = (
        db.query(func.coalesce(func.sum(LedgerEntryLine.amount), Decimal("0.00")))
        .join(LedgerEntry, LedgerEntry.id == LedgerEntryLine.entry_id)
        .filter(LedgerEntryLine.bank_account_id == account.id, LedgerEntry.entry_date <= as_of)
        .scalar()
    )
    return account.opening_balance + booked_sum


@router.get("/accounts/{account_id}/balance", response_model=BankAccountBalanceResponse, responses={**HTTP_404})
def get_account_balance(
    account_id: int,
    as_of: Optional[date] = Query(default=None),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    """Computed balance = opening_balance + sum of *booked* ledger lines up to
    and including `as_of` (default today) — the double-entry ledger's own
    truth, not derived from any bank statement. `last_statement_*` is the
    most recent file import's own reported closing balance for this account
    (None if no import ever carried one); `balance_as_of_last_statement` is
    the *computed* balance evaluated at that same statement date (not
    `as_of`, which is usually today) — that's the pair to actually compare
    for reconciliation, since comparing today's computed balance against a
    past statement's balance would show a spurious mismatch for every
    legitimate booking made since. If everything up to the statement date
    has actually been booked, the two should match — a gap means something's
    still sitting unbooked in the staging queue, or was booked with a wrong
    amount/date."""
    account = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")

    as_of = as_of or datetime.now(UTC).date()

    last_batch = (
        db.query(LedgerImportBatch)
        .filter(
            LedgerImportBatch.bank_account_id == account_id,
            LedgerImportBatch.statement_closing_balance.isnot(None),
        )
        .order_by(LedgerImportBatch.statement_balance_date.desc(), LedgerImportBatch.id.desc())
        .first()
    )
    balance_as_of_last_statement = (
        _computed_balance(db, account, last_batch.statement_balance_date)
        if last_batch and last_batch.statement_balance_date else None
    )

    return BankAccountBalanceResponse(
        account_id=account_id,
        as_of=as_of,
        computed_balance=_computed_balance(db, account, as_of),
        last_statement_balance=last_batch.statement_closing_balance if last_batch else None,
        last_statement_balance_date=last_batch.statement_balance_date if last_batch else None,
        balance_as_of_last_statement=balance_as_of_last_statement,
    )


@router.put("/accounts/{account_id}", response_model=BankAccountResponse, responses={**HTTP_400, **HTTP_404})
def update_account(
    account_id: int,
    body: BankAccountUpdate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Rename an account, toggle `tracked` (e.g. to permanently ignore a private
    account), or set/clear `is_cash_clearing_account` — the one shared
    Kassenbestand counter-account for booking Kassen payouts (#34); setting
    it true here clears it on every other account first, since only one
    can hold it at a time. Only an offline account can hold the flag — a
    real bank account already has its own statement/IBAN and is never
    "cash in hand"."""
    account = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    if body.name is not None:
        account.name = body.name
    if body.tracked is not None:
        account.tracked = body.tracked
    if body.is_cash_clearing_account is not None:
        if body.is_cash_clearing_account:
            if not account.is_offline:
                raise HTTPException(
                    status_code=400,
                    detail="Only an offline account can be the Kassenbestand clearing account",
                )
            db.query(BankAccount).filter(BankAccount.id != account_id).update(
                {BankAccount.is_cash_clearing_account: False}
            )
        account.is_cash_clearing_account = body.is_cash_clearing_account
    db.commit()
    db.refresh(account)
    return account


# --- Ledger categories (Kontenrahmen) ---

@router.get("/categories", response_model=list[LedgerCategoryResponse])
def list_categories(
    include_inactive: bool = Query(default=False),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    q = db.query(LedgerCategory)
    if not include_inactive:
        q = q.filter(LedgerCategory.active.is_(True))
    return q.order_by(LedgerCategory.name).all()


@router.post("/categories", response_model=LedgerCategoryResponse, status_code=201, responses={**HTTP_400, **HTTP_409})
def create_category(
    body: LedgerCategoryCreate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    if db.query(LedgerCategory).filter(LedgerCategory.slug == body.slug).first():
        raise HTTPException(status_code=409, detail="Slug already exists")
    if settings.LEDGER_SPHERES_ENABLED and body.sphere is None:
        raise HTTPException(status_code=400, detail="Sphere is required (Sphärentrennung is enabled)")
    # Ignore any sphere sent while the feature is disabled, so the data stays
    # consistent with the current setting regardless of what a caller sends.
    sphere = body.sphere if settings.LEDGER_SPHERES_ENABLED else None
    category = LedgerCategory(
        name=body.name, slug=body.slug, kind=body.kind, sphere=sphere, active=True
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.put("/categories/{category_id}", response_model=LedgerCategoryResponse, responses={**HTTP_400, **HTTP_404, **HTTP_409})
def update_category(
    category_id: int,
    body: LedgerCategoryUpdate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    category = db.query(LedgerCategory).filter(LedgerCategory.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    reclassifying = body.slug is not None or body.kind is not None or body.sphere is not None
    if reclassifying:
        in_use = db.query(LedgerEntryLine).filter(LedgerEntryLine.category_id == category_id).first()
        if in_use:
            raise HTTPException(
                status_code=409,
                detail="Category already has booked entries — slug/kind/sphere can no longer be changed "
                       "(would retroactively reclassify past bookings). Deactivate it and create a new one instead.",
            )

    if body.slug is not None:
        if db.query(LedgerCategory).filter(LedgerCategory.slug == body.slug, LedgerCategory.id != category_id).first():
            raise HTTPException(status_code=409, detail="Slug already exists")
        category.slug = body.slug
    if body.kind is not None:
        category.kind = body.kind
    if body.sphere is not None:
        if not settings.LEDGER_SPHERES_ENABLED:
            raise HTTPException(status_code=400, detail="Sphären are disabled (LEDGER_SPHERES_ENABLED=false)")
        category.sphere = body.sphere
    if body.name is not None:
        category.name = body.name
    if body.active is not None:
        category.active = body.active
    db.commit()
    db.refresh(category)
    return category


# --- Journal entries (Buchungssätze) ---

@router.get("/entries", response_model=list[LedgerEntryResponse])
def list_entries(
    response: Response,
    year: Optional[int] = Query(default=None),
    bank_account_id: Optional[int] = Query(default=None),
    category_id: Optional[int] = Query(default=None),
    paperless_document_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    """`X-Total-Count` on the response tells the frontend whether more pages
    exist beyond this `limit`/`offset` window — the list itself stays a bare
    JSON array (not an envelope) so existing callers are unaffected."""
    q = db.query(LedgerEntry).options(
        joinedload(LedgerEntry.lines).joinedload(LedgerEntryLine.bank_account),
        joinedload(LedgerEntry.lines).joinedload(LedgerEntryLine.category),
    )
    if year is not None:
        q = q.filter(
            LedgerEntry.entry_date >= f"{year}-01-01", LedgerEntry.entry_date <= f"{year}-12-31"
        )
    if paperless_document_id is not None:
        # Used by the frontend to warn (not block — partial payments against
        # the same invoice are a legitimate reason to link it more than once)
        # when a document is about to be linked a second time.
        q = q.filter(LedgerEntry.paperless_document_id == paperless_document_id)
    if bank_account_id is not None or category_id is not None:
        q = q.join(LedgerEntryLine)
        if bank_account_id is not None:
            q = q.filter(LedgerEntryLine.bank_account_id == bank_account_id)
        if category_id is not None:
            q = q.filter(LedgerEntryLine.category_id == category_id)
    q = q.distinct()
    response.headers["X-Total-Count"] = str(q.count())
    return (
        q.order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.post("/entries", response_model=LedgerEntryResponse, status_code=201, responses={**HTTP_400, **HTTP_404})
def create_entry(
    body: LedgerEntryCreate,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Create a journal entry (Buchungssatz). Lines must sum to zero and each line
    must reference exactly one of an existing bank account or ledger category —
    this is how a single receipt gets split across multiple categories."""
    total = sum((line.amount for line in body.lines), Decimal("0.00"))
    if total != 0:
        raise HTTPException(status_code=400, detail=f"Lines must sum to zero (got {total})")

    for line in body.lines:
        has_account = line.bank_account_id is not None
        has_category = line.category_id is not None
        if has_account == has_category:
            raise HTTPException(
                status_code=400,
                detail="Each line needs exactly one of bank_account_id or category_id",
            )
        if has_account and not db.query(BankAccount).filter(BankAccount.id == line.bank_account_id).first():
            raise HTTPException(status_code=404, detail=f"Bank account {line.bank_account_id} not found")
        if has_category and not db.query(LedgerCategory).filter(LedgerCategory.id == line.category_id).first():
            raise HTTPException(status_code=404, detail=f"Category {line.category_id} not found")

    entry = LedgerEntry(
        entry_date=body.entry_date,
        description=body.description,
        paperless_document_id=body.paperless_document_id,
        created_by=treasurer.get("sub", "unknown"),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    entry.lines = [
        LedgerEntryLine(
            bank_account_id=line.bank_account_id,
            category_id=line.category_id,
            amount=line.amount,
            note=line.note,
        )
        for line in body.lines
    ]
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.post(
    "/entries/{entry_id}/reverse", response_model=LedgerEntryResponse, status_code=201,
    responses={**HTTP_400, **HTTP_404, **HTTP_409},
)
def reverse_entry(
    entry_id: int,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Undo a booking without editing or deleting anything — entries are
    immutable (Key Design Decision #18), same audit-trail philosophy as
    `transactions`. Posts a new entry with every line's amount negated,
    linked back via `reverses_entry_id`. If the original entry came from
    booking an import staging line, that line is reopened (status back to
    `new`, `matched_entry_id` cleared) so it can be re-booked correctly. If
    the original entry came from booking a Kassen payout (#34), its
    `booking_target_payout_id` link is cleared on the *original* so the
    payout shows as open again and can be re-booked — the reversal itself
    keeps no such link (it isn't a payout booking, just its undo).

    A reversal entry can't itself be reversed: that link (`matched_entry_id`
    on a staging line, `booking_target_payout_id` on a payout booking) only
    ever lives on the *original* entry, and gets cleared once it's reversed
    — a reversal-of-a-reversal would financially re-instate the original
    booking without re-establishing that link, leaving the door open to
    double-booking the same import line or payout later. Re-booking the now
    reopened staging line / payout normally is the correct way to "undo an
    accidental reversal", not chaining a second one on top."""
    original = (
        db.query(LedgerEntry).options(joinedload(LedgerEntry.lines))
        .filter(LedgerEntry.id == entry_id).first()
    )
    if not original:
        raise HTTPException(status_code=404, detail="Entry not found")

    if original.reverses_entry_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Cannot reverse a reversal entry — book a fresh entry instead",
        )

    already_reversed = db.query(LedgerEntry).filter(LedgerEntry.reverses_entry_id == entry_id).first()
    if already_reversed:
        raise HTTPException(status_code=409, detail=f"Already reversed by entry {already_reversed.id}")

    reversal = LedgerEntry(
        entry_date=datetime.now(UTC).date(),
        description=f"Storno: {original.description}",
        reverses_entry_id=original.id,
        created_by=treasurer.get("sub", "unknown"),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    reversal.lines = [
        LedgerEntryLine(
            bank_account_id=line.bank_account_id,
            category_id=line.category_id,
            amount=-line.amount,
            note=line.note,
        )
        for line in original.lines
    ]
    db.add(reversal)

    import_line = db.query(LedgerImportLine).filter(LedgerImportLine.matched_entry_id == entry_id).first()
    if import_line:
        import_line.status = LedgerImportStatus.new
        import_line.matched_entry_id = None

    if original.booking_target_payout_id is not None:
        original.booking_target_payout_id = None

    db.commit()
    db.refresh(reversal)
    return reversal


# --- Kassen bridge (legacy NFC booking_targets -> ledger) ---

@router.get("/targets", response_model=list[BookingTargetResponse])
def list_ledger_targets(
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    """Booking targets (Kassen) with their default EÜR-category mapping —
    read-only mirror of `GET /bankomat/targets` scoped to the ledger's own
    viewer role (that endpoint requires a device token or admin, which the
    treasurer/auditor roles don't necessarily have). See #34."""
    return db.query(BookingTarget).order_by(BookingTarget.name).all()


@router.put("/targets/{target_id}", response_model=BookingTargetResponse, responses={**HTTP_404})
def update_ledger_target(
    target_id: int,
    body: LedgerTargetUpdate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Set or clear (`null`) a target's default category, e.g. "Kasse
    Spenden" -> "Spenden" — pre-fills, but never forces, the category when
    booking one of its payouts (`POST /target-payouts/{id}/book`)."""
    target = db.query(BookingTarget).filter(BookingTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Booking target not found")
    if body.default_category_id is not None and not db.query(LedgerCategory).filter(
        LedgerCategory.id == body.default_category_id
    ).first():
        raise HTTPException(status_code=404, detail=f"Category {body.default_category_id} not found")
    target.default_category_id = body.default_category_id
    db.commit()
    db.refresh(target)
    return target


@router.get("/target-payouts", response_model=list[LedgerTargetPayoutResponse])
def list_target_payouts(
    booked: Optional[bool] = Query(default=None),
    target_id: Optional[int] = Query(default=None),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    """Payouts (Auszahlungen) taken from a Kasse, for the treasurer to book
    into the ledger — a full "Aufstellung" by default (`booked` unset), or
    filtered to just the open (`booked=false`) or already-booked
    (`booked=true`) ones. Derived directly from `transactions`/
    `booking_targets` (the legacy NFC-Kassen domain), joined against
    `ledger_entries.booking_target_payout_id` to determine booked status —
    no separate staging table, since `transactions` already is the source
    of truth and is never mutated by this bridge."""
    q = (
        db.query(Transaction, BookingTarget, LedgerEntry.id)
        .join(BookingTarget, Transaction.target_id == BookingTarget.id)
        .outerjoin(LedgerEntry, LedgerEntry.booking_target_payout_id == Transaction.id)
        .filter(Transaction.type == TransactionType.booking_target_payout)
    )
    if target_id is not None:
        q = q.filter(Transaction.target_id == target_id)
    if booked is True:
        q = q.filter(LedgerEntry.id.isnot(None))
    elif booked is False:
        q = q.filter(LedgerEntry.id.is_(None))

    return [
        LedgerTargetPayoutResponse(
            transaction_id=txn.id,
            target_id=target.id,
            target_name=target.name,
            target_slug=target.slug,
            amount=-txn.amount,
            note=txn.note,
            created_at=txn.created_at,
            booked=entry_id is not None,
            ledger_entry_id=entry_id,
        )
        for txn, target, entry_id in q.order_by(Transaction.created_at.desc()).all()
    ]


@router.post(
    "/target-payouts/{transaction_id}/book", response_model=LedgerEntryResponse, status_code=201,
    responses={**HTTP_400, **HTTP_404, **HTTP_409},
)
def book_target_payout(
    transaction_id: int,
    body: BookTargetPayoutRequest,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Book a Kassen payout as a pure transfer from the one shared
    Kassenbestand clearing account to whichever real account the treasurer
    actually deposited the cash into — no category line, since the income
    was already recognized when the cash arrived in the target (folded into
    the EÜR at read time, see `_compute_euer_report`). This is the only
    ledger-side effect of a payout; it does not touch `transactions`/
    `booking_targets` at all (that domain stays untouched, per Key Design
    Decision #18 — this bridge only reads it). Each target's own payouts can
    each be booked to a different destination account — e.g. cash physically
    deposited into a private account, later transferred on to the real
    Vereinskonto via an ordinary manual entry (`POST /entries`), independent
    of this bridge. If the destination account's own bank statement for this
    deposit has already been imported, `matched_import_line_id` links that
    staged line so it's marked booked here too, instead of sitting there as
    a separately-bookable duplicate of the same real-world transaction."""
    txn = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not txn or txn.type != TransactionType.booking_target_payout:
        raise HTTPException(status_code=404, detail="Booking target payout not found")

    already_booked = db.query(LedgerEntry).filter(
        LedgerEntry.booking_target_payout_id == transaction_id
    ).first()
    if already_booked:
        raise HTTPException(status_code=409, detail=f"Already booked as entry {already_booked.id}")

    clearing_account = db.query(BankAccount).filter(BankAccount.is_cash_clearing_account.is_(True)).first()
    if not clearing_account:
        raise HTTPException(
            status_code=400,
            detail="No Kassenbestand clearing account configured — mark one bank account as such first",
        )
    destination = db.query(BankAccount).filter(BankAccount.id == body.bank_account_id).first()
    if not destination:
        raise HTTPException(status_code=404, detail=f"Bank account {body.bank_account_id} not found")
    if destination.id == clearing_account.id:
        raise HTTPException(status_code=400, detail="Destination account cannot be the clearing account itself")

    target = db.query(BookingTarget).filter(BookingTarget.id == txn.target_id).first()
    amount = -txn.amount  # payout transactions are stored negative; the booking is the positive cash inflow

    matched_line = None
    if body.matched_import_line_id is not None:
        matched_line = db.query(LedgerImportLine).filter(
            LedgerImportLine.id == body.matched_import_line_id
        ).first()
        if not matched_line:
            raise HTTPException(status_code=404, detail="Matched import line not found")
        if matched_line.status == LedgerImportStatus.booked:
            raise HTTPException(status_code=400, detail="Matched import line is already booked")
        if matched_line.bank_account_id != destination.id:
            raise HTTPException(
                status_code=400,
                detail="Matched import line must belong to the selected destination account",
            )
        if matched_line.amount != amount:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Matched import line's amount ({matched_line.amount}) doesn't match "
                    f"the payout amount ({amount})"
                ),
            )

    entry = LedgerEntry(
        entry_date=body.entry_date or txn.created_at.date(),
        description=body.description or f"Kassenauszahlung: {target.name if target else txn.target_id}",
        booking_target_payout_id=transaction_id,
        created_by=treasurer.get("sub", "unknown"),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    entry.lines = [
        LedgerEntryLine(bank_account_id=destination.id, amount=amount),
        LedgerEntryLine(bank_account_id=clearing_account.id, amount=-amount),
    ]
    db.add(entry)
    db.flush()

    if matched_line:
        matched_line.status = LedgerImportStatus.booked
        matched_line.matched_entry_id = entry.id

    db.commit()
    db.refresh(entry)
    return entry


# --- EÜR report ---

def _compute_euer_report(db: Session, year: int) -> EuerReportResponse:
    """Einnahmenüberschussrechnung: category totals for a year, grouped by
    category (which carries its EÜR-Sphäre). Categorization correctness (which
    category, which Sphäre) should be signed off by the Verein's Kassenprüfer/
    Steuerberater — this only aggregates what has been booked. Shared by the
    JSON endpoint and the PDF export so both always agree.

    In the double-entry ledger, a category line balances the opposite-signed
    bank line (e.g. a 50€ topup is bank +50 / income-category -50). Income
    category raw totals are therefore negative and are flipped here so both
    `total_income` and `total_expense` come out non-negative, matching how an
    EÜR is normally read (net_result = total_income - total_expense).

    Also folds in cash that arrived in a Kassen booking target this year
    (`topup`/`booking_target_topup`/`booking_target_adjustment` transactions
    whose target has a `default_category_id`) — see Key Design Decision #34.
    That cash is never written as its own `ledger_entries` row (it's the same
    money `booking_targets`/`transactions` already track in full, and
    duplicating it into a second ledger table would just be a second figure
    that can drift out of sync); it's folded in here, at read time, exactly
    like a category line would be. `contribution = -transaction.amount`
    mirrors the same sign convention as a real category line for both
    directions: a topup/positive adjustment (amount > 0, more cash in)
    contributes negative (more income, same as any other deposit); a
    shortfall (a negative `booking_target_adjustment` amount) contributes
    positive (less income, i.e. a write-off)."""
    lines = (
        db.query(LedgerEntryLine)
        .join(LedgerEntry)
        .join(LedgerCategory)
        .filter(
            LedgerEntry.entry_date >= f"{year}-01-01",
            LedgerEntry.entry_date <= f"{year}-12-31",
            LedgerEntryLine.category_id.isnot(None),
        )
        .options(joinedload(LedgerEntryLine.category))
        .all()
    )

    raw_totals: dict[int, Decimal] = {}
    categories: dict[int, LedgerCategory] = {}
    for line in lines:
        raw_totals[line.category_id] = raw_totals.get(line.category_id, Decimal("0.00")) + line.amount
        categories[line.category_id] = line.category

    target_events = (
        db.query(Transaction.amount, BookingTarget.default_category_id)
        .join(BookingTarget, Transaction.target_id == BookingTarget.id)
        .filter(
            Transaction.created_at >= f"{year}-01-01",
            Transaction.created_at < f"{year + 1}-01-01",
            Transaction.type.in_([
                TransactionType.topup,
                TransactionType.booking_target_topup,
                TransactionType.booking_target_adjustment,
            ]),
            BookingTarget.default_category_id.isnot(None),
        )
        .all()
    )
    for amount, category_id in target_events:
        if category_id not in categories:
            category = db.query(LedgerCategory).filter(LedgerCategory.id == category_id).first()
            if not category:
                continue  # defensive only — default_category_id always resolves in practice
            categories[category_id] = category
        raw_totals[category_id] = raw_totals.get(category_id, Decimal("0.00")) - amount

    category_totals = []
    for cat_id, raw_total in raw_totals.items():
        category = categories[cat_id]
        # Flip income (naturally Haben/negative) to a positive display total; expense
        # lines are already positive (Soll) on the category side.
        display_total = -raw_total if category.kind == LedgerCategoryKind.income else raw_total
        category_totals.append(
            EuerCategoryTotal(
                category_id=cat_id,
                name=category.name,
                slug=category.slug,
                kind=category.kind,
                sphere=category.sphere,
                total=display_total,
            )
        )
    category_totals.sort(key=lambda c: c.name)

    total_income = sum(
        (c.total for c in category_totals if c.kind == LedgerCategoryKind.income), Decimal("0.00")
    )
    total_expense = sum(
        (c.total for c in category_totals if c.kind == LedgerCategoryKind.expense), Decimal("0.00")
    )

    return EuerReportResponse(
        year=year,
        categories=category_totals,
        total_income=total_income,
        total_expense=total_expense,
        net_result=total_income - total_expense,
    )


@router.get("/report/euer", response_model=EuerReportResponse)
def euer_report(
    year: int = Query(...),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    return _compute_euer_report(db, year)


_EUER_TYP_PATH = Path(__file__).parent.parent.parent.parent / "statements" / "euer.typ"


def _compile_euer_pdf(report: EuerReportResponse, lang: str) -> bytes:
    import typst  # optional dependency

    _ = get_translator(lang)
    spheres_enabled = settings.LEDGER_SPHERES_ENABLED

    def _item(c: EuerCategoryTotal) -> dict:
        row = {"name": c.name, "line_amount": f"{c.total:.2f} {settings.CURRENCY}"}
        if spheres_enabled:
            row["sphere_label"] = _(f"ledger.sphere_{c.sphere.value}") if c.sphere else _("ledger.sphere_none")
        return row

    labels = {
        "col_category": _("common.name"),
        "col_sphere": _("ledger.col_sphere"),
        "col_amount": _("ledger.col_total"),
        "section_income": _("ledger.pdf_section_income"),
        "section_expense": _("ledger.pdf_section_expense"),
        "total_income": _("ledger.report_total_income"),
        "total_expense": _("ledger.report_total_expense"),
        "net_result": _("ledger.report_net_result"),
        "generated": _("ledger.pdf_generated"),
        "empty": _("ledger.pdf_empty"),
        "disclaimer": _("ledger.pdf_disclaimer"),
    }
    data = {
        "title": _("ledger.pdf_title"),
        "period": _("ledger.pdf_period", year=report.year),
        "spheres_enabled": spheres_enabled,
        "income_items": [_item(c) for c in report.categories if c.kind == LedgerCategoryKind.income],
        "expense_items": [_item(c) for c in report.categories if c.kind == LedgerCategoryKind.expense],
        "total_income": f"{report.total_income:.2f} {settings.CURRENCY}",
        "total_expense": f"{report.total_expense:.2f} {settings.CURRENCY}",
        "net_result": f"{report.net_result:.2f} {settings.CURRENCY}",
        "labels": labels,
    }

    font_paths = [settings.TYPST_FONT_DIR] if settings.TYPST_FONT_DIR else []
    return typst.compile(
        input=str(_EUER_TYP_PATH),
        sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
        font_paths=font_paths,
    )


@router.get("/report/euer/pdf", response_class=Response, responses={**HTTP_400})
def euer_report_pdf(
    year: int = Query(...),
    lang: str = Query(default="de"),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    report = _compute_euer_report(db, year)
    try:
        pdf = _compile_euer_pdf(report, lang)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}") from e
    filename = f"euer_{year}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Bank statement import (Phase 2: file upload; FinTS live-pull is Phase 3) ---

def _compute_dedup_hash(bank_account_id: int, booking_date, amount: Decimal, purpose_text, counterparty_iban) -> str:
    raw = "|".join([
        str(bank_account_id),
        booking_date.isoformat(),
        str(amount),
        purpose_text or "",
        counterparty_iban or "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _store_import_lines(db: Session, batch: LedgerImportBatch, account_id: int, parsed_lines: list) -> LedgerImportSummary:
    """Dedup + stage a batch of parsed statement lines against one account.

    Shared by the file-upload import and the FinTS live-pull — both produce
    the same `ParsedStatementLine` shape (see `app/services/bank_statement.py`),
    so this is the single place the dedup rule lives. Every line is stored
    (even duplicates, for auditability): matched first by (bank reference,
    amount), otherwise by a content hash — both with a multiset count so two
    genuinely identical same-day transactions aren't wrongly collapsed into
    one.

    The reference match is keyed on (reference, amount), not the reference
    alone — found against real bank data (2026-09-11): a bank reference
    (EREF/AcctSvcrRef) isn't always unique per transaction in practice. Banks
    fill in a placeholder — commonly the SEPA/ISO-20022 constant
    `NOTPROVIDED`, or MT940's `NONREF` — for any payment that didn't carry an
    explicit end-to-end reference, and *every* such transaction then shares
    that same string. Matching on the reference alone flagged the vast
    majority of a real import as `duplicate` even on the very first import,
    since the reference by itself said nothing about which transaction it
    was. Requiring the amount to agree too keeps the reference match's
    intended role (catching a duplicate whose purpose text drifted slightly
    between exports, which the pure content hash below wouldn't) without
    collapsing every same-day `NOTPROVIDED` payment into "already seen."

    Two upfront queries (not one per line) establish how many times each
    reference/hash has already been imported *before* this batch, so the
    per-line loop below is a pure in-memory decision with no risk of an
    autoflush mid-loop silently changing what "already imported" means.
    """
    hashes = {
        _compute_dedup_hash(account_id, line.booking_date, line.amount, line.purpose_text, line.counterparty_iban)
        for line in parsed_lines
    }
    existing_hash_counts: dict[str, int] = dict(
        db.query(LedgerImportLine.dedup_hash, func.count())
        .filter(LedgerImportLine.bank_account_id == account_id, LedgerImportLine.dedup_hash.in_(hashes))
        .group_by(LedgerImportLine.dedup_hash)
        .all()
    ) if hashes else {}

    ref_keys = {(line.bank_reference, line.amount) for line in parsed_lines if line.bank_reference}
    existing_ref_counts: dict[tuple[str, Decimal], int] = {}
    if ref_keys:
        references = {ref for ref, _ in ref_keys}
        existing_ref_counts = {
            (ref, amount): count
            for ref, amount, count in
            db.query(LedgerImportLine.bank_reference, LedgerImportLine.amount, func.count())
            .filter(LedgerImportLine.bank_account_id == account_id, LedgerImportLine.bank_reference.in_(references))
            .group_by(LedgerImportLine.bank_reference, LedgerImportLine.amount)
            .all()
        }

    seen_hash_counts: dict[str, int] = {}
    seen_ref_counts: dict[tuple[str, Decimal], int] = {}
    new_count = duplicate_count = 0

    for line in parsed_lines:
        dedup_hash = _compute_dedup_hash(
            account_id, line.booking_date, line.amount, line.purpose_text, line.counterparty_iban
        )
        if line.bank_reference:
            ref_key = (line.bank_reference, line.amount)
            seen_so_far = seen_ref_counts.get(ref_key, 0)
            is_duplicate = seen_so_far < existing_ref_counts.get(ref_key, 0)
            seen_ref_counts[ref_key] = seen_so_far + 1
        else:
            seen_so_far = seen_hash_counts.get(dedup_hash, 0)
            is_duplicate = seen_so_far < existing_hash_counts.get(dedup_hash, 0)
            seen_hash_counts[dedup_hash] = seen_so_far + 1

        status = LedgerImportStatus.duplicate if is_duplicate else LedgerImportStatus.new
        if is_duplicate:
            duplicate_count += 1
        else:
            new_count += 1

        db.add(LedgerImportLine(
            batch_id=batch.id,
            bank_account_id=account_id,
            booking_date=line.booking_date,
            amount=line.amount,
            purpose_text=line.purpose_text,
            counterparty_name=line.counterparty_name,
            counterparty_iban=line.counterparty_iban,
            bank_reference=line.bank_reference,
            dedup_hash=dedup_hash,
            status=status,
        ))

    db.commit()
    return LedgerImportSummary(
        batch_id=batch.id,
        new_count=new_count,
        duplicate_count=duplicate_count,
        total_count=len(parsed_lines),
    )


@router.post(
    "/import/file", response_model=LedgerImportSummary, status_code=201,
    responses={**HTTP_400, **HTTP_404},
)
async def import_bank_statement_file(
    request: Request,
    bank_account_id: int = Query(...),
    filename: str = Query(...),
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Upload an MT940 or CAMT.053 export for one bank account. Raw file bytes
    as the request body (no multipart) — a browser can pass a `File` object
    straight to `fetch()`.

    Every parsed line is stored (even duplicates, for auditability): matched
    first by bank reference, otherwise by a content hash with a multiset
    count so two genuinely identical same-day transactions aren't wrongly
    collapsed into one. See `app/services/bank_statement.py` for parsing and
    the plan doc for the full dedup rationale.
    """
    account = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    if account.is_offline:
        raise HTTPException(status_code=400, detail="Offline accounts have no bank statement to import")
    if not account.tracked:
        raise HTTPException(status_code=400, detail="Account is marked as ignored (tracked=false)")

    content = await request.body()
    try:
        parsed = parse_statement_file(content)
    except UnsupportedStatementFormat as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not account_identifier_matches(parsed.iban, account.iban):
        raise HTTPException(
            status_code=400,
            detail=f"File is for IBAN {parsed.iban}, not the selected account's {account.iban}",
        )

    batch = LedgerImportBatch(
        bank_account_id=account.id,
        source=LedgerImportSource.file,
        filename=filename,
        statement_opening_balance=parsed.opening_balance,
        statement_closing_balance=parsed.closing_balance,
        statement_balance_date=parsed.closing_balance_date,
        imported_by=treasurer.get("sub", "unknown"),
    )
    db.add(batch)
    db.flush()
    return _store_import_lines(db, batch, account.id, parsed.lines)


# --- CSV import (no standard schema across banks — column mapping is user-driven) ---

@router.post("/import/csv/preview", response_model=CsvPreviewResponse, responses={**HTTP_400})
async def preview_csv_import(
    request: Request,
    delimiter: Optional[str] = Query(default=None),
    _treasurer: dict = Depends(require_treasurer_user),
):
    """Step 1: sniff the delimiter (unless given), return the file's own
    columns, a few sample rows, and a best-effort name-based mapping guess —
    never applied unseen, just a starting point for the mapping form."""
    content = await request.body()
    try:
        used_delimiter, columns, sample_rows = preview_csv(content, delimiter)
    except UnsupportedStatementFormat as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return CsvPreviewResponse(
        delimiter=used_delimiter, columns=columns, sample_rows=sample_rows,
        guessed_mapping=guess_csv_mapping(columns),
    )


@router.post(
    "/import/csv", response_model=LedgerImportSummary, status_code=201,
    responses={**HTTP_400, **HTTP_404},
)
async def import_csv_statement(
    request: Request,
    bank_account_id: int = Query(...),
    filename: str = Query(...),
    delimiter: str = Query(default=";"),
    decimal_separator: str = Query(default=","),
    date_format: str = Query(default="%d.%m.%Y"),
    booking_date_column: str = Query(...),
    amount_column: str = Query(...),
    purpose_column: Optional[str] = Query(default=None),
    counterparty_name_column: Optional[str] = Query(default=None),
    counterparty_iban_column: Optional[str] = Query(default=None),
    bank_reference_column: Optional[str] = Query(default=None),
    own_iban_column: Optional[str] = Query(default=None),
    balance_after_column: Optional[str] = Query(default=None),
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Step 2: import with a confirmed column mapping. Same staging/dedup
    pipeline as `POST /import/file` (`_store_import_lines`) — only the
    parsing differs. The mapping is remembered on the account
    (`BankAccount.csv_mapping`) so the next upload from the same bank starts
    pre-filled instead of from scratch. The IBAN-match safety check only
    applies when `own_iban_column` was actually mapped — many CSV exports
    don't carry the account's own IBAN at all, unlike MT940/CAMT.053 where
    it's mandatory."""
    account = db.query(BankAccount).filter(BankAccount.id == bank_account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    if account.is_offline:
        raise HTTPException(status_code=400, detail="Offline accounts have no bank statement to import")
    if not account.tracked:
        raise HTTPException(status_code=400, detail="Account is marked as ignored (tracked=false)")

    mapping = CsvColumnMapping(
        delimiter=delimiter, decimal_separator=decimal_separator, date_format=date_format,
        booking_date_column=booking_date_column, amount_column=amount_column,
        purpose_column=purpose_column, counterparty_name_column=counterparty_name_column,
        counterparty_iban_column=counterparty_iban_column, bank_reference_column=bank_reference_column,
        own_iban_column=own_iban_column, balance_after_column=balance_after_column,
    )
    content = await request.body()
    try:
        parsed = parse_csv_statement(content, mapping)
    except UnsupportedStatementFormat as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if parsed.iban and not account_identifier_matches(parsed.iban, account.iban):
        raise HTTPException(
            status_code=400,
            detail=f"File is for IBAN {parsed.iban}, not the selected account's {account.iban}",
        )

    account.csv_mapping = asdict(mapping)
    batch = LedgerImportBatch(
        bank_account_id=account.id,
        source=LedgerImportSource.file,
        filename=filename,
        statement_closing_balance=parsed.closing_balance,
        statement_balance_date=parsed.closing_balance_date,
        imported_by=treasurer.get("sub", "unknown"),
    )
    db.add(batch)
    db.flush()
    return _store_import_lines(db, batch, account.id, parsed.lines)


@router.get("/import/lines", response_model=list[LedgerImportLineResponse])
def list_import_lines(
    response: Response,
    status: list[LedgerImportStatus] = Query(default=[LedgerImportStatus.new]),
    bank_account_id: Optional[int] = Query(default=None),
    amount: Optional[Decimal] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    """`X-Total-Count` on the response tells the frontend whether more pages
    exist beyond this `limit`/`offset` window — the list itself stays a bare
    JSON array (not an envelope) so existing callers are unaffected.

    `amount` (exact match) is used by the transfer-booking UI to find a
    counter-account's candidate matching line — a plain transfer moves the
    full amount, so no tolerance/range filtering is needed. That search
    passes `status=new&status=duplicate` (repeat the query param for each
    value): a line flagged `duplicate` by the (heuristic, false-positive-
    prone — see #19) dedup check is still perfectly bookable, so excluding
    it from transfer candidates would make a real transfer unmatchable
    whenever either side happened to get flagged."""
    q = db.query(LedgerImportLine)
    if status:
        q = q.filter(LedgerImportLine.status.in_(status))
    if bank_account_id is not None:
        q = q.filter(LedgerImportLine.bank_account_id == bank_account_id)
    if amount is not None:
        q = q.filter(LedgerImportLine.amount == amount)
    response.headers["X-Total-Count"] = str(q.count())
    return (
        q.order_by(LedgerImportLine.booking_date.desc(), LedgerImportLine.id.desc())
        .offset(offset).limit(limit).all()
    )


@router.post(
    "/import/lines/{line_id}/book", response_model=LedgerEntryResponse, status_code=201,
    responses={**HTTP_400, **HTTP_404},
)
def book_import_line(
    line_id: int,
    body: LedgerImportLineBookRequest,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Book a staging line: the bank leg is taken from the staging line itself
    (fixed amount), `category_lines` supply the rest of the split — same
    balance-to-zero rule as `POST /entries`, just with the bank side implied.
    A split line may reference another bank account instead of a category
    (booking a transfer); at most one may, and `matched_import_line_id` can
    then link the other account's own staged line for the same transfer so
    it's booked in the same step instead of being reviewed (and risking a
    double-booking) separately later."""
    staging = db.query(LedgerImportLine).filter(LedgerImportLine.id == line_id).first()
    if not staging:
        raise HTTPException(status_code=404, detail="Import line not found")
    if staging.status == LedgerImportStatus.booked:
        raise HTTPException(status_code=400, detail="Already booked")

    category_total = sum((line.amount for line in body.category_lines), Decimal("0.00"))
    if category_total != -staging.amount:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Category lines must sum to {-staging.amount} to balance the "
                f"bank amount of {staging.amount} (got {category_total})"
            ),
        )

    transfer_lines = []
    for line in body.category_lines:
        has_category = line.category_id is not None
        has_account = line.bank_account_id is not None
        if has_category == has_account:
            raise HTTPException(
                status_code=400,
                detail="Each split line needs exactly one of category_id or bank_account_id",
            )
        if has_category:
            if not db.query(LedgerCategory).filter(LedgerCategory.id == line.category_id).first():
                raise HTTPException(status_code=404, detail=f"Category {line.category_id} not found")
        else:
            if not db.query(BankAccount).filter(BankAccount.id == line.bank_account_id).first():
                raise HTTPException(status_code=404, detail=f"Bank account {line.bank_account_id} not found")
            transfer_lines.append(line)

    if len(transfer_lines) > 1:
        raise HTTPException(status_code=400, detail="At most one split line may be a transfer (bank_account_id)")
    if body.matched_import_line_id is not None and not transfer_lines:
        raise HTTPException(
            status_code=400,
            detail="matched_import_line_id requires a transfer split line (bank_account_id)",
        )

    matched_line = None
    if body.matched_import_line_id is not None:
        transfer_line = transfer_lines[0]
        matched_line = (
            db.query(LedgerImportLine).filter(LedgerImportLine.id == body.matched_import_line_id).first()
        )
        if not matched_line:
            raise HTTPException(status_code=404, detail="Matched import line not found")
        if matched_line.status == LedgerImportStatus.booked:
            raise HTTPException(status_code=400, detail="Matched import line is already booked")
        if matched_line.bank_account_id != transfer_line.bank_account_id:
            raise HTTPException(
                status_code=400,
                detail="Matched import line must belong to the selected counter-account",
            )
        if matched_line.amount != transfer_line.amount:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Matched import line's amount ({matched_line.amount}) doesn't match "
                    f"the transfer amount ({transfer_line.amount}) — a transfer moves the full amount"
                ),
            )

    entry = LedgerEntry(
        entry_date=staging.booking_date,
        description=body.description,
        paperless_document_id=body.paperless_document_id,
        created_by=treasurer.get("sub", "unknown"),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    entry.lines = [
        LedgerEntryLine(bank_account_id=staging.bank_account_id, amount=staging.amount, note=staging.purpose_text),
    ] + [
        LedgerEntryLine(
            category_id=line.category_id, bank_account_id=line.bank_account_id, amount=line.amount, note=line.note,
        )
        for line in body.category_lines
    ]
    db.add(entry)
    db.flush()

    staging.status = LedgerImportStatus.booked
    staging.matched_entry_id = entry.id
    if matched_line:
        matched_line.status = LedgerImportStatus.booked
        matched_line.matched_entry_id = entry.id
    db.commit()
    db.refresh(entry)
    return entry


@router.post("/import/lines/{line_id}/ignore", response_model=MessageResponse, responses={**HTTP_400, **HTTP_404})
def ignore_import_line(
    line_id: int,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    staging = db.query(LedgerImportLine).filter(LedgerImportLine.id == line_id).first()
    if not staging:
        raise HTTPException(status_code=404, detail="Import line not found")
    if staging.status == LedgerImportStatus.booked:
        raise HTTPException(status_code=400, detail="Already booked, cannot ignore")
    staging.status = LedgerImportStatus.ignored
    db.commit()
    return {"detail": "Import line ignored"}


@router.post("/import/lines/{line_id}/reset", response_model=MessageResponse, responses={**HTTP_400, **HTTP_404})
def reset_import_line(
    line_id: int,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Move a `duplicate` or `ignored` line back to `new` — "Kein Duplikat"
    (a dedup false positive, see #19) or restoring an ignored line. `booked`
    is rejected (use `POST /entries/{id}/reverse` on its entry instead — that
    path also reopens the staging line automatically, see #28); a line
    already `new` is a no-op 400 rather than silently succeeding."""
    staging = db.query(LedgerImportLine).filter(LedgerImportLine.id == line_id).first()
    if not staging:
        raise HTTPException(status_code=404, detail="Import line not found")
    if staging.status not in (LedgerImportStatus.duplicate, LedgerImportStatus.ignored):
        raise HTTPException(status_code=400, detail=f"Cannot reset a line with status '{staging.status.value}'")
    staging.status = LedgerImportStatus.new
    db.commit()
    return {"detail": "Import line reset to new"}


# --- Paperless-ngx document search (proxy, read-only) ---

@router.get("/paperless/search", response_model=list[PaperlessDocumentResult])
def search_paperless(
    q: str = Query(..., min_length=1),
    _viewer: dict = Depends(require_ledger_viewer_user),
):
    return paperless.search_documents(q)


# --- FinTS bank presets (server/BLZ/name only — never login/PIN) ---

@router.get("/fints/presets", response_model=list[FintsBankPresetResponse])
def list_fints_presets(
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    return db.query(FintsBankPreset).order_by(FintsBankPreset.name).all()


@router.post("/fints/presets", response_model=FintsBankPresetResponse, status_code=201)
def create_fints_preset(
    body: FintsBankPresetCreate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    preset = FintsBankPreset(name=body.name, server=body.server, bank_identifier=body.bank_identifier)
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return preset


@router.delete("/fints/presets/{preset_id}", status_code=204, responses={**HTTP_404})
def delete_fints_preset(
    preset_id: int,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    preset = db.query(FintsBankPreset).filter(FintsBankPreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    db.delete(preset)
    db.commit()


# --- FinTS live-pull (Phase 3): manual only, nothing persisted between requests ---

def _annotate_accounts(db: Session, accounts: list[dict]) -> list[dict]:
    """Match FinTS-reported accounts against known bank_accounts by IBAN, so
    the wizard can show "already tracked" vs. "new — register or ignore"
    (the case this whole IBAN-based tracking design was originally for: one
    FinTS access exposing both the Vereinskonto and an unrelated private
    account)."""
    ibans = [a["iban"] for a in accounts if a.get("iban")]
    known = {
        acc.iban: acc
        for acc in db.query(BankAccount).filter(BankAccount.iban.in_(ibans)).all()
    } if ibans else {}
    result = []
    for a in accounts:
        match = known.get(a.get("iban"))
        result.append({
            **a,
            "tracked": match.tracked if match else None,
            "bank_account_id": match.id if match else None,
        })
    return result


def _finalize_transactions_result(db: Session, treasurer: dict, result: dict) -> dict:
    """If `result` is a completed transaction fetch, stage it (source='fints')
    through the same dedup path as a file upload and return a
    `LedgerImportSummary`-shaped dict. Otherwise pass through unchanged
    (still tan_required/decoupled/accounts) — a TAN can be required at the
    account-listing step, the transaction-fetch step, or not at all, and any
    of `/fints/start`, `/fints/tan`, `/fints/poll` or the import endpoint
    itself might be the call that finally resolves into transaction data."""
    if result.get("status") != "transactions":
        return result

    iban = result["iban"]
    account = db.query(BankAccount).filter(BankAccount.iban == iban).first()
    if not account:
        raise HTTPException(
            status_code=404,
            detail=f"Account {iban} is not registered — add it under Bankkonten first",
        )
    if not account.tracked:
        raise HTTPException(status_code=400, detail="Account is marked as ignored (tracked=false)")

    batch = LedgerImportBatch(
        bank_account_id=account.id,
        source=LedgerImportSource.fints,
        imported_by=treasurer.get("sub", "unknown"),
    )
    db.add(batch)
    db.flush()
    return _store_import_lines(db, batch, account.id, result["lines"])


def _postprocess_step(db: Session, treasurer: dict, result: dict) -> dict:
    if result.get("status") == "accounts":
        result["accounts"] = _annotate_accounts(db, result["accounts"])
        return result
    return _finalize_transactions_result(db, treasurer, result)


def _run_fints(fn, *args):
    """Run a fints_client call, turning both our own control-flow exceptions
    and any unexpected error surfaced by the FinTS protocol/network exchange
    (wrong PIN, bank offline, connection drop, ...) into a clean HTTP error
    instead of a raw 500 — talking to an external bank over a live protocol
    is expected to fail sometimes, and that's not a bug in this app."""
    import fints.exceptions

    try:
        return fn(*args)
    except fints_client.FinTSNotConfigured as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except fints_client.FinTSSessionError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except fints.exceptions.FinTSError as e:
        raise HTTPException(status_code=502, detail=f"Bank connection failed: {e}") from e


@router.post("/fints/start", response_model=None, responses={**HTTP_400, **HTTP_502})
def fints_start(
    body: FinTSStartRequest,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Connect to the bank and list its accounts. Body is never persisted —
    server URL, BLZ, login and PIN exist only for this request and the short
    in-memory session that follows (see app/services/fints_client.py)."""
    result = _run_fints(fints_client.start_dialog, body.server, body.bank_identifier, body.login, body.pin)
    return _postprocess_step(db, treasurer, result)


@router.post("/fints/tan", response_model=None, responses={**HTTP_400, **HTTP_404, **HTTP_502})
def fints_submit_tan(
    body: FinTSTanRequest,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Resolve a pending TAN challenge, for whichever operation was pending
    (account listing or transaction fetch) — see `_finalize_transactions_result`."""
    result = _run_fints(fints_client.submit_tan, body.session_id, body.tan)
    return _postprocess_step(db, treasurer, result)


@router.post("/fints/poll", response_model=None, responses={**HTTP_404, **HTTP_502})
def fints_poll(
    body: FinTSPollRequest,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """For decoupled (pushTAN-app) challenges: re-check without a new TAN."""
    result = _run_fints(fints_client.poll_decoupled, body.session_id)
    return _postprocess_step(db, treasurer, result)


@router.post(
    "/fints/accounts/{iban}/import", response_model=None,
    responses={**HTTP_400, **HTTP_404, **HTTP_502},
)
def fints_import_account(
    iban: str,
    body: FinTSImportRequest,
    treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Fetch transactions for one account discovered in a prior /fints/start
    (or /fints/tan) call and stage them exactly like a file upload. May come
    back as another TAN challenge instead of the final `LedgerImportSummary`
    — some banks require a TAN for the transaction read specifically, even
    if the account listing didn't."""
    result = _run_fints(fints_client.start_transactions, body.session_id, iban, body.date_from, body.date_to)
    return _finalize_transactions_result(db, treasurer, result)
