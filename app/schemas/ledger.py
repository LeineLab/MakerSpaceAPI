from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.ledger import LedgerCategoryKind, LedgerImportStatus, LedgerSphere


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
    name: Optional[str] = None
    tracked: Optional[bool] = None


class BankAccountResponse(BaseModel):
    id: int
    iban: Optional[str]
    name: str
    is_offline: bool
    tracked: bool
    opening_balance: Decimal = Field(examples=[Decimal("0.00")])
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LedgerCategoryCreate(BaseModel):
    """`sphere` is required when settings.LEDGER_SPHERES_ENABLED is True (checked
    in the endpoint), otherwise ignored and stored as None."""
    name: str = Field(examples=["Mitgliedsbeiträge"])
    slug: str = Field(examples=["mitgliedsbeitraege"])
    kind: LedgerCategoryKind
    sphere: Optional[LedgerSphere] = None


class LedgerCategoryUpdate(BaseModel):
    name: Optional[str] = None
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
    created_by: str
    created_at: datetime
    lines: list[LedgerEntryLineResponse]

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
    category_id: int
    amount: Decimal = Field(examples=[Decimal("50.00")])
    note: Optional[str] = None


class LedgerImportLineBookRequest(BaseModel):
    """Books a staging line: the bank leg is taken from the staging line itself
    (amount fixed), `category_lines` supply the rest — their amounts must sum
    to the negative of the staging line's amount so the resulting entry
    balances to zero, exactly like a manually created LedgerEntryCreate."""
    description: str = Field(examples=["Mitgliedsbeitrag Max Mustermann"])
    paperless_document_id: Optional[str] = None
    category_lines: list[LedgerImportLineCategorySplit] = Field(min_length=1)


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
