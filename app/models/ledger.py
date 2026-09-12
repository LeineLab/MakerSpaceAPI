import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class LedgerCategoryKind(str, enum.Enum):
    income = "income"
    expense = "expense"


class LedgerImportSource(str, enum.Enum):
    file = "file"
    fints = "fints"


class LedgerImportStatus(str, enum.Enum):
    new = "new"
    duplicate = "duplicate"
    booked = "booked"
    ignored = "ignored"


class LedgerSphere(str, enum.Enum):
    """Sphären-Trennung required for a gemeinnütziger Verein's EÜR."""
    ideell = "ideell"
    vermoegensverwaltung = "vermoegensverwaltung"
    zweckbetrieb = "zweckbetrieb"
    wirtschaftlicher_geschaeftsbetrieb = "wirtschaftlicher_geschaeftsbetrieb"


class LedgerReserveKind(str, enum.Enum):
    """The four Rücklagenarten a gemeinnütziger Verein may legally form under
    §62 AO. Kept as German legal terms (not translated), same convention as
    LedgerSphere — these are specific tax-law categories, not generic labels."""
    frei = "frei"
    zweckgebunden = "zweckgebunden"
    betriebsmittel = "betriebsmittel"
    wiederbeschaffung = "wiederbeschaffung"


class BankAccount(Base):
    """Despite the name, also holds "offline" accounts (`is_offline=True`) —
    manual-balance holders with no IBAN and no statement import, e.g. a
    prepaid credit balance with a service provider (domain registrar, etc.).
    They participate in the ledger exactly like a real bank account (transfer
    money in, book invoices out of them) but never appear in the file/FinTS
    import account pickers, since there's no statement to pull. Enforced by
    the CHECK below: an account is either a real one (`iban` required) or an
    offline one (`iban` must be NULL) — never both/neither.
    """
    __tablename__ = "bank_accounts"
    __table_args__ = (
        CheckConstraint(
            "(is_offline = 0 AND iban IS NOT NULL) OR (is_offline = 1 AND iban IS NULL)",
            name="ck_bank_account_offline_has_no_iban",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    iban: Mapped[Optional[str]] = mapped_column(String(34), nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_offline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    # Remembered CSV column mapping (app.services.bank_statement.CsvColumnMapping,
    # as a dict) from this account's last successful CSV import — banks have no
    # standard CSV schema, so the treasurer maps columns once and it's reused
    # (still editable) on the next upload instead of starting from scratch.
    csv_mapping: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # The one shared "Kassenbestand" clearing account used as the technical
    # counter-leg when booking a Kassen payout (see Key Design Decision #34)
    # — at most one account has this set at a time (enforced in the API
    # layer, not the DB: setting it on one account clears it on every other).
    is_cash_clearing_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class FintsBankPreset(Base):
    """Non-secret FinTS connection presets a treasurer can save for reuse:
    server URL + Bankleitzahl + a display name. Login and PIN are never
    stored anywhere (typed fresh on every /fints/start call, held only in
    the in-memory session — see app/services/fints_client.py); this table
    only saves the two fields that rarely change and are tedious to look up
    each time."""
    __tablename__ = "fints_bank_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    server: Mapped[str] = mapped_column(String(255), nullable=False)
    bank_identifier: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class LedgerCategory(Base):
    __tablename__ = "ledger_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    kind: Mapped[LedgerCategoryKind] = mapped_column(Enum(LedgerCategoryKind), nullable=False)
    # Nullable: Sphärentrennung only applies to gemeinnützige Vereine. Required
    # when settings.LEDGER_SPHERES_ENABLED is True (enforced in the API layer),
    # always NULL when that's disabled (e.g. a commercial installation).
    sphere: Mapped[Optional[LedgerSphere]] = mapped_column(Enum(LedgerSphere), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Migration 0017: terms matched case-insensitively (substring) against a
    # staging line's purpose_text to compute a suggested_category_id — advisory
    # only, never enforced. No two categories may have overlapping keywords
    # (checked in the API layer, not the DB) — see Key Design Decision #45.
    match_keywords: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    # Set on a reversal entry (created by POST /ledger/entries/{id}/reverse) to
    # the original entry it cancels out — entries are otherwise immutable, so
    # "undoing" a booking means posting an offsetting entry, not editing/
    # deleting anything. RESTRICT: a reversed entry can never itself be
    # deleted (it never can be, there's no delete endpoint — belt and braces).
    reverses_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    lines: Mapped[list["LedgerEntryLine"]] = relationship(
        "LedgerEntryLine", back_populates="entry", cascade="all, delete-orphan"
    )


class LedgerEntryLine(Base):
    __tablename__ = "ledger_entry_lines"
    __table_args__ = (
        CheckConstraint(
            "(bank_account_id IS NOT NULL AND category_id IS NULL) OR "
            "(bank_account_id IS NULL AND category_id IS NOT NULL)",
            name="ck_ledger_entry_line_one_side",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ledger_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bank_account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=True
    )
    category_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ledger_categories.id", ondelete="RESTRICT"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Moved here (migration 0016) from ledger_entries — a single entry-level
    # field couldn't represent several invoices paid in one bank debit, one
    # ledger_entry_lines row per invoice. Only meaningful on a category-side
    # line (a bank_account line is a cash movement, not an invoice); the API
    # layer doesn't enforce that, same as `note`.
    paperless_document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    entry: Mapped["LedgerEntry"] = relationship("LedgerEntry", back_populates="lines")
    bank_account: Mapped[Optional["BankAccount"]] = relationship("BankAccount")
    category: Mapped[Optional["LedgerCategory"]] = relationship("LedgerCategory")


class LedgerTargetPayoutEntry(Base):
    """Many-to-many link between a Kassen payout (`transactions.id`,
    type=booking_target_payout) and the ledger_entries that (partially)
    book it. Replaces the old 1:1 `ledger_entries.booking_target_payout_id`
    column (migration 0015) — a payout can now be split across several
    transfers (e.g. a bank transfer-amount limit forced multiple wires) or
    several payouts can be bundled into one transfer. `amount` is the slice
    of this payout covered by this entry: the entry's own leg amount when
    the entry covers exactly one payout (whether the full amount or a
    partial split), or the payout's own full amount when the entry bundles
    several payouts together (bundling always covers each payout in full —
    no partial bundling). See Key Design Decision #38."""
    __tablename__ = "ledger_target_payout_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ledger_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)


class LedgerAsset(Base):
    """Anlagevermögen: links an already-booked, category-side
    `ledger_entry_lines` row (the real cash outflow at purchase) to a
    depreciation schedule. `entry_line_id` (UNIQUE) is the line whose full
    amount is excluded from the EÜR in its booking year once linked — see Key
    Design Decision #35 — and replaced, at report-computation time, by the
    linear/monatsgenau AfA amount computed from `acquisition_cost`/
    `useful_life_years`/`acquisition_date` for each year of the asset's
    useful life. No yearly booking rows are ever created for this (same
    read-time-aggregation approach as the Kassen bridge, #34) — deleting a
    `LedgerAsset` simply reverts its line to being counted normally again.
    `category_id` is the AfA target category (e.g. "Abschreibungen"), which
    may differ from whatever category the original purchase was booked
    against. `disposed_at` stops future AfA from the month after disposal;
    it does NOT auto-book a write-off of any remaining book value — that's
    left to the treasurer via a normal entry (see docstring on the API
    endpoint), same as the deliberately-manual sale-proceeds handling in the
    Kassen bridge (#34)."""
    __tablename__ = "ledger_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_line_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ledger_entry_lines.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False)
    acquisition_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    useful_life_years: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ledger_categories.id", ondelete="RESTRICT"), nullable=False
    )
    disposed_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    entry_line: Mapped["LedgerEntryLine"] = relationship("LedgerEntryLine")
    category: Mapped["LedgerCategory"] = relationship("LedgerCategory")


class LedgerReserve(Base):
    """A Rücklage (§62 AO) — a named pot that part of the Verein's already-
    recognized surplus is earmarked into. This is NOT a real cash movement
    (the money stays in whichever bank account it already sits in); it's a
    logical allocation used to compile the Mittelverwendungsrechnung (see Key
    Design Decision #36). `sphere` is purely informational here (unlike
    LedgerCategory, it's never required even when LEDGER_SPHERES_ENABLED) —
    it doesn't affect the report computation. `purpose`/`target_date` are the
    concrete-plan-and-timeframe a `zweckgebunden` reserve legally needs;
    validated as required for that kind in the API layer, not the DB."""
    __tablename__ = "ledger_reserves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[LedgerReserveKind] = mapped_column(Enum(LedgerReserveKind), nullable=False)
    sphere: Mapped[Optional[LedgerSphere]] = mapped_column(Enum(LedgerSphere), nullable=True)
    purpose: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class LedgerReserveMovement(Base):
    """A Zuführung (positive amount) or Auflösung/Entnahme (negative amount)
    against one LedgerReserve, dated and freely correctable (unlike
    `ledger_entries`, these aren't real financial bookings — just a note on
    top of already-recognized surplus, see LedgerReserve's docstring)."""
    __tablename__ = "ledger_reserve_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reserve_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ledger_reserves.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    reserve: Mapped["LedgerReserve"] = relationship("LedgerReserve")


class LedgerAuditReport(Base):
    """A Kassenprüfungsprotokoll — the annual (or ad-hoc) audit report from
    the Verein's Kassenprüfer, covering a period and recommending (or not)
    the Vorstand's Entlastung. `auditors` is free text, not a FK to `users`
    — Kassenprüfer are elected members and not necessarily app users at all.
    Writing these is gated by `require_auditor_writer_user` (admin or
    explicit auditor-group membership, deliberately NOT a plain treasurer —
    see Key Design Decision #37) since a treasurer authoring the report that
    audits their own bookkeeping would defeat the point. Freely editable/
    deletable like LedgerReserve(Movement) — not part of the immutable
    financial audit trail itself, just a record about it."""
    __tablename__ = "ledger_audit_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    audit_date: Mapped[date] = mapped_column(Date, nullable=False)
    auditors: Mapped[str] = mapped_column(String(255), nullable=False)
    findings: Mapped[Optional[str]] = mapped_column(String(4000), nullable=True)
    recommends_discharge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paperless_document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class LedgerImportBatch(Base):
    __tablename__ = "ledger_import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bank_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source: Mapped[LedgerImportSource] = mapped_column(Enum(LedgerImportSource), nullable=False)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # The bank's own reported balances, when the statement carries them (MT940
    # :60F:/:62F:, CAMT.053 <Bal> OPBD/CLBD) — for manual reconciliation via
    # GET /ledger/accounts/{id}/balance, not compared automatically here (the
    # lines from this very batch aren't booked yet at import time).
    statement_opening_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    statement_closing_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    statement_balance_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    imported_by: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class LedgerImportLine(Base):
    __tablename__ = "ledger_import_lines"
    __table_args__ = (
        # Deliberately NOT a UNIQUE constraint: a re-imported line with a bank_reference
        # that already exists is still inserted (status='duplicate', for auditability),
        # it's just excluded from the default review view. Dedup itself — by reference
        # or by dedup_hash — happens in application code before each insert (see
        # import_bank_statement_file() in app/api/v1/ledger.py).
        Index("ix_ledger_import_lines_account_reference", "bank_account_id", "bank_reference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ledger_import_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bank_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    booking_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    purpose_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    counterparty_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    counterparty_iban: Mapped[Optional[str]] = mapped_column(String(34), nullable=True)
    bank_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[LedgerImportStatus] = mapped_column(
        Enum(LedgerImportStatus), nullable=False, default=LedgerImportStatus.new, index=True
    )
    matched_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ledger_entries.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    batch: Mapped["LedgerImportBatch"] = relationship("LedgerImportBatch")
    bank_account: Mapped["BankAccount"] = relationship("BankAccount")
    matched_entry: Mapped[Optional["LedgerEntry"]] = relationship("LedgerEntry")
