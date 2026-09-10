import hashlib
import json
from datetime import UTC, datetime
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
from app.schemas.common import HTTP_400, HTTP_404, HTTP_409, HTTP_502, MessageResponse
from app.schemas.ledger import (
    BankAccountCreate,
    BankAccountResponse,
    BankAccountUpdate,
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
    PaperlessDocumentResult,
)
from app.services import fints_client, paperless
from app.services.bank_statement import UnsupportedStatementFormat, parse_statement_file
from app.web.i18n import get_translator

router = APIRouter()


@router.get("/config", response_model=LedgerConfigResponse)
def get_config(_viewer: dict = Depends(require_ledger_viewer_user)):
    """Frontend-facing feature flags (e.g. whether to show the Sphäre field)."""
    return LedgerConfigResponse(
        spheres_enabled=settings.LEDGER_SPHERES_ENABLED,
        paperless_enabled=paperless.is_configured(),
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


@router.put("/accounts/{account_id}", response_model=BankAccountResponse, responses={**HTTP_404})
def update_account(
    account_id: int,
    body: BankAccountUpdate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    """Rename an account or toggle `tracked` (e.g. to permanently ignore a private account)."""
    account = db.query(BankAccount).filter(BankAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    if body.name is not None:
        account.name = body.name
    if body.tracked is not None:
        account.tracked = body.tracked
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


@router.put("/categories/{category_id}", response_model=LedgerCategoryResponse, responses={**HTTP_404})
def update_category(
    category_id: int,
    body: LedgerCategoryUpdate,
    _treasurer: dict = Depends(require_treasurer_user),
    db: Session = Depends(get_db),
):
    category = db.query(LedgerCategory).filter(LedgerCategory.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
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
    year: Optional[int] = Query(default=None),
    bank_account_id: Optional[int] = Query(default=None),
    category_id: Optional[int] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    q = db.query(LedgerEntry).options(
        joinedload(LedgerEntry.lines).joinedload(LedgerEntryLine.bank_account),
        joinedload(LedgerEntry.lines).joinedload(LedgerEntryLine.category),
    )
    if year is not None:
        q = q.filter(
            LedgerEntry.entry_date >= f"{year}-01-01", LedgerEntry.entry_date <= f"{year}-12-31"
        )
    if bank_account_id is not None or category_id is not None:
        q = q.join(LedgerEntryLine)
        if bank_account_id is not None:
            q = q.filter(LedgerEntryLine.bank_account_id == bank_account_id)
        if category_id is not None:
            q = q.filter(LedgerEntryLine.category_id == category_id)
    return (
        q.order_by(LedgerEntry.entry_date.desc(), LedgerEntry.id.desc())
        .distinct()
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
    EÜR is normally read (net_result = total_income - total_expense)."""
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
    (even duplicates, for auditability): matched first by bank reference,
    otherwise by a content hash with a multiset count so two genuinely
    identical same-day transactions aren't wrongly collapsed into one.

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

    references = {line.bank_reference for line in parsed_lines if line.bank_reference}
    existing_references: set[str] = {
        row[0] for row in
        db.query(LedgerImportLine.bank_reference)
        .filter(LedgerImportLine.bank_account_id == account_id, LedgerImportLine.bank_reference.in_(references))
        .all()
    } if references else set()

    seen_hash_counts: dict[str, int] = {}
    seen_references: set[str] = set()
    new_count = duplicate_count = 0

    for line in parsed_lines:
        dedup_hash = _compute_dedup_hash(
            account_id, line.booking_date, line.amount, line.purpose_text, line.counterparty_iban
        )
        if line.bank_reference:
            is_duplicate = line.bank_reference in existing_references or line.bank_reference in seen_references
            seen_references.add(line.bank_reference)
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

    if parsed.iban != account.iban:
        raise HTTPException(
            status_code=400,
            detail=f"File is for IBAN {parsed.iban}, not the selected account's {account.iban}",
        )

    batch = LedgerImportBatch(
        bank_account_id=account.id,
        source=LedgerImportSource.file,
        filename=filename,
        imported_by=treasurer.get("sub", "unknown"),
    )
    db.add(batch)
    db.flush()
    return _store_import_lines(db, batch, account.id, parsed.lines)


@router.get("/import/lines", response_model=list[LedgerImportLineResponse])
def list_import_lines(
    status: Optional[LedgerImportStatus] = Query(default=LedgerImportStatus.new),
    bank_account_id: Optional[int] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    _viewer: dict = Depends(require_ledger_viewer_user),
    db: Session = Depends(get_db),
):
    q = db.query(LedgerImportLine)
    if status is not None:
        q = q.filter(LedgerImportLine.status == status)
    if bank_account_id is not None:
        q = q.filter(LedgerImportLine.bank_account_id == bank_account_id)
    return q.order_by(LedgerImportLine.booking_date.desc(), LedgerImportLine.id.desc()).limit(limit).all()


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
    balance-to-zero rule as `POST /entries`, just with the bank side implied."""
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
    for line in body.category_lines:
        if not db.query(LedgerCategory).filter(LedgerCategory.id == line.category_id).first():
            raise HTTPException(status_code=404, detail=f"Category {line.category_id} not found")

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
        LedgerEntryLine(category_id=line.category_id, amount=line.amount, note=line.note)
        for line in body.category_lines
    ]
    db.add(entry)
    db.flush()

    staging.status = LedgerImportStatus.booked
    staging.matched_entry_id = entry.id
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
