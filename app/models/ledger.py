import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
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


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    paperless_document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
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

    entry: Mapped["LedgerEntry"] = relationship("LedgerEntry", back_populates="lines")
    bank_account: Mapped[Optional["BankAccount"]] = relationship("BankAccount")
    category: Mapped[Optional["LedgerCategory"]] = relationship("LedgerCategory")


class LedgerImportBatch(Base):
    __tablename__ = "ledger_import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bank_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bank_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source: Mapped[LedgerImportSource] = mapped_column(Enum(LedgerImportSource), nullable=False)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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
