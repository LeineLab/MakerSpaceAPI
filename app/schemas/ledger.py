from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.ledger import LedgerCategoryKind, LedgerImportStatus, LedgerReserveKind, LedgerSphere


class BankAccountCreate(BaseModel):
    """`iban` is required unless `is_offline` is set, in which case it must be
    omitted (checked in the endpoint) — an offline account is a manual-balance
    holder with no statement to import (e.g. a prepaid credit balance with a
    service provider), not a real bank account."""
    iban: Optional[str] = Field(default=None, examples=["DE02120300000000202051"])
    name: str = Field(examples=["Vereinskonto Sparkasse"])
    is_offline: bool = False
    opening_balance: Decimal = Field(default=Decimal("0.00"), examples=[Decimal("0.00")])
    tracked: bool = True


class BankAccountUpdate(BaseModel):
    """`is_cash_clearing_account: true` marks this account as the one shared
    "Kassenbestand" counter-account for booking Kassen payouts — setting it
    clears the flag on every other account (checked in the endpoint), since
    there's only ever one."""
    name: Optional[str] = None
    tracked: Optional[bool] = None
    is_cash_clearing_account: Optional[bool] = None


class BankAccountResponse(BaseModel):
    id: int
    iban: Optional[str]
    name: str
    is_offline: bool
    tracked: bool
    opening_balance: Decimal = Field(examples=[Decimal("0.00")])
    csv_mapping: Optional[dict] = None
    is_cash_clearing_account: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BankAccountBalanceResponse(BaseModel):
    """Computed from the double-entry ledger itself (opening_balance + booked
    lines up to `as_of`) — independent of what any bank statement claims.
    `last_statement_*` is the most recent import batch's own reported
    closing balance for this account (None if no import ever carried one),
    for the treasurer to eyeball against `computed_balance`."""
    account_id: int
    as_of: date
    computed_balance: Decimal = Field(examples=[Decimal("1234.56")])
    last_statement_balance: Optional[Decimal] = None
    last_statement_balance_date: Optional[date] = None
    # The *computed* balance evaluated at last_statement_balance_date (not at
    # `as_of`) — the actually-comparable figure for reconciliation. None
    # whenever last_statement_balance_date is None.
    balance_as_of_last_statement: Optional[Decimal] = None


class CsvPreviewResponse(BaseModel):
    """Step 1 of the CSV import flow: shows the file's own columns and a few
    sample rows, plus a best-effort name-based mapping guess — the frontend
    pre-fills the mapping form from `guessed_mapping` but always shows the
    sample rows so the treasurer confirms it before importing anything."""
    delimiter: str
    columns: list[str]
    sample_rows: list[list[str]]
    guessed_mapping: dict[str, Optional[str]]


class LedgerCategoryCreate(BaseModel):
    """`sphere` is required when settings.LEDGER_SPHERES_ENABLED is True (checked
    in the endpoint), otherwise ignored and stored as None."""
    name: str = Field(examples=["Mitgliedsbeiträge"])
    slug: str = Field(examples=["mitgliedsbeitraege"])
    kind: LedgerCategoryKind
    sphere: Optional[LedgerSphere] = None


class LedgerCategoryUpdate(BaseModel):
    """`slug`/`kind`/`sphere` reclassify the category and are only accepted
    while it isn't referenced by any booked line yet (checked in the
    endpoint) — changing them after the fact would retroactively reclassify
    already-booked history in the EÜR report. `name`/`active` have no such
    restriction; they're just a display label and a retire switch."""
    name: Optional[str] = None
    slug: Optional[str] = None
    kind: Optional[LedgerCategoryKind] = None
    sphere: Optional[LedgerSphere] = None
    active: Optional[bool] = None


class LedgerCategoryResponse(BaseModel):
    id: int
    name: str
    slug: str
    kind: LedgerCategoryKind
    sphere: Optional[LedgerSphere]
    active: bool

    model_config = ConfigDict(from_attributes=True)


class LedgerConfigResponse(BaseModel):
    """Frontend-facing feature flags for the ledger module."""
    spheres_enabled: bool
    paperless_enabled: bool
    paperless_url: Optional[str] = None
    fints_enabled: bool


class LedgerEntryLineCreate(BaseModel):
    """Exactly one of bank_account_id/category_id must be set (checked in the endpoint)."""
    bank_account_id: Optional[int] = None
    category_id: Optional[int] = None
    amount: Decimal = Field(examples=[Decimal("-42.00")])
    note: Optional[str] = None


class LedgerEntryLineResponse(BaseModel):
    id: int
    bank_account_id: Optional[int]
    category_id: Optional[int]
    amount: Decimal = Field(examples=[Decimal("-42.00")])
    note: Optional[str]
    bank_account: Optional[BankAccountResponse] = None
    category: Optional[LedgerCategoryResponse] = None

    model_config = ConfigDict(from_attributes=True)


class LedgerEntryCreate(BaseModel):
    """A journal entry (Buchungssatz). Lines must sum to zero (double-entry)."""
    entry_date: date
    description: str = Field(examples=["Wareneinkauf Getränke"])
    paperless_document_id: Optional[str] = None
    lines: list[LedgerEntryLineCreate] = Field(min_length=2)


class LedgerEntryResponse(BaseModel):
    id: int
    entry_date: date
    description: str
    paperless_document_id: Optional[str]
    reverses_entry_id: Optional[int]
    created_by: str
    created_at: datetime
    lines: list[LedgerEntryLineResponse]

    model_config = ConfigDict(from_attributes=True)


class LedgerTargetUpdate(BaseModel):
    """Set (or clear, with `null`) a booking target's default EÜR category —
    used to pre-fill (not force) the category when booking one of its
    payouts. See Key Design Decision #34."""
    default_category_id: Optional[int] = None


class LedgerTargetPayoutResponse(BaseModel):
    """A `booking_target_payout` transaction from the legacy NFC-Kassen
    system, shown here so the treasurer can book it into the ledger (or see
    how much of it already has been). No category here — the income was
    already recognized when the cash arrived in the target (see Key Design
    Decision #34); booking a payout is a pure transfer. `booked_amount`/
    `remaining_amount` (see #38) replace a plain `booked` bool because a
    payout can now be partially booked (split across several transfers);
    `booked` is `True` only once `remaining_amount <= 0`. `entry_ids` lists
    every ledger entry that covers some part of this payout — usually one,
    more than one only for a split booking."""
    transaction_id: int
    target_id: int
    target_name: str
    target_slug: str
    amount: Decimal = Field(examples=[Decimal("20.00")])
    note: Optional[str] = None
    created_at: datetime
    booked_amount: Decimal = Field(examples=[Decimal("20.00")])
    remaining_amount: Decimal = Field(examples=[Decimal("0.00")])
    booked: bool
    entry_ids: list[int] = []


class BookTargetPayoutsRequest(BaseModel):
    """Books one or more Kassen payouts as a single pure-transfer leg from
    the shared Kassenbestand clearing account to `bank_account_id` — no
    category, since the income was already recognized when the cash arrived
    in the target(s). See Key Design Decision #38 for the split/bundle
    rules this enforces:

    - A single `payout_transaction_ids` entry may be booked for less than
      its full remaining amount, to support splitting one payout across
      several real transfers (e.g. a bank transfer-amount limit).
    - Multiple `payout_transaction_ids` bundle several payouts into one
      transfer — `amount` must equal the exact sum of their (so-far
      entirely unbooked) amounts; no partial bundling.

    `entry_date`/`description` default to a generated label (the single
    payout's own date, or today for a bundle of several). `matched_import_
    line_id` links the destination account's own not-yet-booked staged line
    for this deposit (found via `GET /ledger/import/lines?bank_account_id=&
    amount=&status=new&status=duplicate`, or now also `?q=` free-text) —
    same "explicitly matched, not guessed" pattern as transfer-booking an
    import line (#32)."""
    payout_transaction_ids: list[int] = Field(min_length=1)
    bank_account_id: int
    amount: Decimal = Field(gt=0, examples=[Decimal("20.00")])
    entry_date: Optional[date] = None
    description: Optional[str] = None
    matched_import_line_id: Optional[int] = None


class LedgerAssetCreate(BaseModel):
    """Links an already-booked, category-side ledger_entry_line (found via the
    entries list) as a capital asset. `acquisition_cost` is taken from the
    line's own amount, not re-typed. `category_id` is the AfA target
    category (e.g. "Abschreibungen") — may differ from whatever category the
    purchase itself was originally booked against. `acquisition_date`
    defaults to the entry's own `entry_date`."""
    name: str = Field(examples=["Lasercutter Speedy 400"])
    entry_line_id: int
    useful_life_years: int = Field(gt=0, examples=[7])
    category_id: int
    acquisition_date: Optional[date] = None
    notes: Optional[str] = None


class LedgerAssetUpdate(BaseModel):
    """`disposed_at` (set or cleared with `null`) stops future AfA after that
    month — it does not book a write-off of any remaining book value, see
    the model docstring. Every field may be corrected at any time; since AfA
    is computed at report time (not stored per-year), a correction here
    retroactively changes the AfA shown in past EÜR reports too — same as
    fixing any other historical data."""
    name: Optional[str] = None
    useful_life_years: Optional[int] = Field(default=None, gt=0)
    category_id: Optional[int] = None
    acquisition_date: Optional[date] = None
    disposed_at: Optional[date] = None
    clear_disposed_at: bool = False
    notes: Optional[str] = None


class LedgerAssetResponse(BaseModel):
    id: int
    name: str
    entry_line_id: int
    acquisition_date: date
    acquisition_cost: Decimal = Field(examples=[Decimal("3500.00")])
    useful_life_years: int
    category_id: int
    category: Optional[LedgerCategoryResponse] = None
    disposed_at: Optional[date] = None
    notes: Optional[str] = None
    accumulated_depreciation: Decimal = Field(examples=[Decimal("500.00")])
    book_value: Decimal = Field(examples=[Decimal("3000.00")])

    model_config = ConfigDict(from_attributes=True)


class EuerCategoryTotal(BaseModel):
    category_id: int
    name: str
    slug: str
    kind: LedgerCategoryKind
    sphere: Optional[LedgerSphere]
    total: Decimal = Field(examples=[Decimal("1234.56")])


class EuerReportResponse(BaseModel):
    year: int
    categories: list[EuerCategoryTotal]
    total_income: Decimal = Field(examples=[Decimal("5000.00")])
    total_expense: Decimal = Field(examples=[Decimal("3200.00")])
    net_result: Decimal = Field(examples=[Decimal("1800.00")])


class LedgerReserveCreate(BaseModel):
    """`purpose`/`target_date` are required when `kind == 'zweckgebunden'` —
    a zweckgebundene Rücklage legally needs a concrete plan and timeframe,
    checked in the endpoint (not the DB). `sphere` is purely informational
    here (never required, unlike LedgerCategory)."""
    name: str = Field(examples=["Wiederbeschaffung Lasercutter"])
    kind: LedgerReserveKind
    sphere: Optional[LedgerSphere] = None
    purpose: Optional[str] = None
    target_date: Optional[date] = None


class LedgerReserveUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[LedgerReserveKind] = None
    sphere: Optional[LedgerSphere] = None
    purpose: Optional[str] = None
    target_date: Optional[date] = None
    clear_target_date: bool = False


class LedgerReserveResponse(BaseModel):
    id: int
    name: str
    kind: LedgerReserveKind
    sphere: Optional[LedgerSphere]
    purpose: Optional[str]
    target_date: Optional[date]
    balance: Decimal = Field(examples=[Decimal("500.00")])

    model_config = ConfigDict(from_attributes=True)


class LedgerReserveMovementCreate(BaseModel):
    """Positive `amount` = Zuführung (earmarking funds into the reserve),
    negative = Auflösung/Entnahme (releasing them back for general use)."""
    reserve_id: int
    movement_date: date
    amount: Decimal = Field(examples=[Decimal("500.00")])
    note: Optional[str] = None


class LedgerReserveMovementUpdate(BaseModel):
    movement_date: Optional[date] = None
    amount: Optional[Decimal] = None
    note: Optional[str] = None


class LedgerReserveMovementResponse(BaseModel):
    id: int
    reserve_id: int
    movement_date: date
    amount: Decimal = Field(examples=[Decimal("500.00")])
    note: Optional[str]
    created_by: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MittelverwendungReportResponse(BaseModel):
    """Rücklagen/Mittelverwendungsrechnung (§55/§62 AO): a running Vortrag of
    surplus that must be used in a timely manner, net of what's legitimately
    earmarked into reserves. `cumulative_relevant_net_result` sums the EÜR
    net result (income minus expense) of every category NOT in the
    wirtschaftlicher Geschäftsbetrieb Sphäre, from the beginning of the
    ledger's history through 31.12 of `year` — that Sphäre's after-tax
    surplus is treated as more freely usable and excluded here; this
    simplification (like the EÜR-Sphäre mapping itself) should be checked
    against the Verein's own situation by its Kassenprüfer/Steuerberater.
    `available_funds = cumulative_relevant_net_result - cumulative_zufuehrungen
    + cumulative_aufloesungen` — the amount still awaiting timely use."""
    year: int
    cumulative_relevant_net_result: Decimal = Field(examples=[Decimal("4200.00")])
    cumulative_reserve_zufuehrungen: Decimal = Field(examples=[Decimal("1500.00")])
    cumulative_reserve_aufloesungen: Decimal = Field(examples=[Decimal("300.00")])
    available_funds: Decimal = Field(examples=[Decimal("3000.00")])
    reserves: list[LedgerReserveResponse]


class LedgerAuditReportCreate(BaseModel):
    """A Kassenprüfungsprotokoll. `auditors` is free text (e.g. "Max
    Mustermann, Erika Musterfrau") — Kassenprüfer are elected members, not
    necessarily app users. Writing requires the narrower auditor-only
    permission (see Key Design Decision #37), not just any treasurer."""
    period_start: date
    period_end: date
    audit_date: date
    auditors: str = Field(examples=["Max Mustermann, Erika Musterfrau"])
    findings: Optional[str] = None
    recommends_discharge: bool = True
    paperless_document_id: Optional[str] = None


class LedgerAuditReportUpdate(BaseModel):
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    audit_date: Optional[date] = None
    auditors: Optional[str] = None
    findings: Optional[str] = None
    recommends_discharge: Optional[bool] = None
    paperless_document_id: Optional[str] = None
    clear_paperless_document_id: bool = False


class LedgerAuditReportResponse(BaseModel):
    id: int
    period_start: date
    period_end: date
    audit_date: date
    auditors: str
    findings: Optional[str]
    recommends_discharge: bool
    paperless_document_id: Optional[str]
    created_by: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LedgerImportLineResponse(BaseModel):
    id: int
    batch_id: int
    bank_account_id: int
    booking_date: date
    amount: Decimal = Field(examples=[Decimal("-42.00")])
    purpose_text: Optional[str]
    counterparty_name: Optional[str]
    counterparty_iban: Optional[str]
    bank_reference: Optional[str]
    status: LedgerImportStatus
    matched_entry_id: Optional[int]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LedgerImportSummary(BaseModel):
    """Result of a file upload: how many staging lines were created and how
    many were recognized as already-imported duplicates."""
    batch_id: int
    new_count: int
    duplicate_count: int
    total_count: int


class LedgerImportLineCategorySplit(BaseModel):
    """Exactly one of category_id/bank_account_id must be set (checked in the
    endpoint). bank_account_id books a transfer leg to another account
    instead of a category — at most one split line may use it (a booking is
    either a transfer or a category split, not a mix)."""
    category_id: Optional[int] = None
    bank_account_id: Optional[int] = None
    amount: Decimal = Field(examples=[Decimal("50.00")])
    note: Optional[str] = None


class LedgerImportLineBookRequest(BaseModel):
    """Books a staging line: the bank leg is taken from the staging line itself
    (amount fixed), `category_lines` supply the rest — their amounts must sum
    to the negative of the staging line's amount so the resulting entry
    balances to zero, exactly like a manually created LedgerEntryCreate.

    `matched_import_line_id`: for a transfer (one split line has
    bank_account_id set), optionally the id of the *other* account's own
    staged import line representing the same real-world movement — found via
    `GET /ledger/import/lines?bank_account_id=&amount=`, filtered to an exact
    amount match (a plain transfer moves the full amount, no partial). That
    line is booked too, linked to the same entry, instead of being left to
    show up again (and get double-booked) once reviewed on its own."""
    description: str = Field(examples=["Mitgliedsbeitrag Max Mustermann"])
    paperless_document_id: Optional[str] = None
    category_lines: list[LedgerImportLineCategorySplit] = Field(min_length=1)
    matched_import_line_id: Optional[int] = None


class PaperlessDocumentResult(BaseModel):
    id: int
    title: str
    created: Optional[str] = None


# --- FinTS bank presets: server/BLZ/name only, saved for reuse — never credentials ---

class FintsBankPresetCreate(BaseModel):
    name: str = Field(examples=["Beispielbank"])
    server: str = Field(examples=["https://banking-fints.example.com/fints30"])
    bank_identifier: str = Field(examples=["12030000"])


class FintsBankPresetResponse(BaseModel):
    id: int
    name: str
    server: str
    bank_identifier: str

    model_config = ConfigDict(from_attributes=True)


# --- FinTS live-pull (Phase 3) — nothing here is ever persisted ---

class FinTSStartRequest(BaseModel):
    server: str = Field(examples=["https://banking-fints.example.com/fints30"])
    bank_identifier: str = Field(examples=["12030000"])
    login: str
    pin: str


class FinTSTanRequest(BaseModel):
    session_id: str
    tan: str = ""


class FinTSPollRequest(BaseModel):
    session_id: str


class FinTSImportRequest(BaseModel):
    session_id: str
    date_from: date
    date_to: date


class FinTSAccountResult(BaseModel):
    iban: str
    bic: Optional[str] = None
    tracked: Optional[bool] = None  # None = IBAN not registered as a bank_account yet
    bank_account_id: Optional[int] = None


class FinTSStepResponse(BaseModel):
    """One step of the wizard: either a TAN challenge to resolve, or (on
    success starting a dialog) the list of accounts found for that access."""
    session_id: str
    status: str  # "tan_required" | "decoupled" | "accounts"
    challenge: Optional[str] = None
    accounts: Optional[list[FinTSAccountResult]] = None
