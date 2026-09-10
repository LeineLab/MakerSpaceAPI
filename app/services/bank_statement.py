"""Parse bank statement exports (MT940, CAMT.053) into a common shape.

Both formats vary across banks (SEPA vs. legacy field codes, schema versions),
so extraction of everything except date/amount/purpose is best-effort — the
raw purpose text is always preserved so nothing is lost for manual review.
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from xml.etree import ElementTree


class UnsupportedStatementFormat(Exception):
    pass


@dataclass
class ParsedStatementLine:
    booking_date: date
    amount: Decimal
    purpose_text: str | None
    counterparty_name: str | None
    counterparty_iban: str | None
    bank_reference: str | None


@dataclass
class ParsedStatement:
    iban: str
    lines: list[ParsedStatementLine]
    # The bank's own reported balances, for reconciliation against the sum of
    # what's actually been booked in the ledger — None when the file doesn't
    # carry them (not all exports do). Not compared automatically at import
    # time: the just-staged lines haven't been booked yet at that point, so
    # an immediate comparison would almost always show a spurious mismatch.
    # See GET /ledger/accounts/{id}/balance for the on-demand comparison.
    opening_balance: Optional[Decimal] = None
    closing_balance: Optional[Decimal] = None
    closing_balance_date: Optional[date] = None


_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")


def _looks_like_iban(value: str) -> bool:
    return bool(_IBAN_RE.match(value))


def account_identifier_matches(raw_identifier: str, account_iban: str) -> bool:
    """True if `raw_identifier` — whatever a statement's account field
    actually contains — refers to `account_iban`.

    Most banks put a real IBAN in MT940's :25: field / CAMT's <IBAN>, but some
    still export the legacy "BLZ/Kontonummer" format there instead (observed
    directly against a real bank, 2026-09). We deliberately don't synthesize
    an IBAN from BLZ+Kontonummer to handle that — proper IBAN construction
    needs the mod-97 check-digit algorithm plus, for a handful of German
    banks, non-standard conversion rules, which is more machinery than "does
    this file belong to the account the treasurer selected" warrants. Instead
    we decompose the already-known, already-valid `account_iban` (standard
    German layout: 2-letter country + 2 check digits + 8-digit BLZ + 10-digit
    Kontonummer) and compare against that — nothing is computed, only sliced.
    """
    raw = raw_identifier.replace(" ", "").upper()
    iban = account_iban.replace(" ", "").upper()

    if _looks_like_iban(raw):
        return raw == iban

    if not iban.startswith("DE") or len(iban) != 22:
        return False  # legacy-format comparison only implemented for standard-layout German IBANs

    blz, kontonummer = iban[4:12], iban[12:22].lstrip("0")

    parts = raw.replace("/", " ").split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    # BLZ is always exactly 8 digits in Germany — use that to disambiguate
    # the field order rather than assuming "BLZ/Kontonummer" specifically.
    a, b = parts
    file_blz, file_kontonummer = (a, b) if len(a) == 8 else (b, a)
    return file_blz == blz and file_kontonummer.lstrip("0") == kontonummer


def parse_statement_file(content: bytes) -> ParsedStatement:
    stripped = content.lstrip()
    if stripped.startswith(b":20:"):
        return _parse_mt940(content)
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<Document"):
        return _parse_camt053(content)
    raise UnsupportedStatementFormat(
        "Unbekanntes Dateiformat (weder MT940 noch CAMT.053 erkannt)"
    )


def lines_from_mt940_transactions(transactions) -> list[ParsedStatementLine]:
    """Convert an iterable of `mt940.models.Transaction` into `ParsedStatementLine`s.

    Shared by the file-upload path (`_parse_mt940` below) and the FinTS
    live-pull (`app/services/fints_client.py::fetch_transactions`), since
    `FinTS3PinTanClient.get_transactions()` returns the same transaction
    shape when the bank speaks MT940 over FinTS (the common case).
    """
    lines = []
    for tx in transactions:
        d = tx.data
        booking_date = d.get("entry_date") or d["date"]
        purpose = d.get("purpose") or d.get("posting_text")
        counterparty_name = d.get("applicant_name") or d.get("recipient_name")
        counterparty_iban = d.get("gvc_applicant_iban") or d.get("applicant_iban")
        bank_reference = d.get("end_to_end_reference") or d.get("bank_reference")
        lines.append(ParsedStatementLine(
            booking_date=date(booking_date.year, booking_date.month, booking_date.day),
            amount=Decimal(str(d["amount"].amount)),
            purpose_text=purpose,
            counterparty_name=counterparty_name,
            counterparty_iban=counterparty_iban,
            bank_reference=bank_reference,
        ))
    return lines


def _parse_mt940(content: bytes) -> ParsedStatement:
    import mt940  # optional dependency, only needed for this import path

    try:
        statement = mt940.parse(content)
    except UnicodeDecodeError:
        statement = mt940.parse(content, encoding="latin-1")

    iban = statement.data.get("account_identification")
    if not iban:
        raise UnsupportedStatementFormat("MT940-Datei enthält keine Konto-IBAN (Feld :25:)")

    opening = statement.data.get("final_opening_balance")
    closing = statement.data.get("final_closing_balance")
    return ParsedStatement(
        iban=iban,
        lines=lines_from_mt940_transactions(statement),
        opening_balance=Decimal(str(opening.amount.amount)) if opening else None,
        closing_balance=Decimal(str(closing.amount.amount)) if closing else None,
        closing_balance_date=closing.date if closing else None,
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(elem: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _path(elem: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    for name in names:
        if elem is None:
            return None
        elem = _child(elem, name)
    return elem


def _text(elem: ElementTree.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    text = elem.text.strip()
    return text or None


def _parse_camt053(content: bytes) -> ParsedStatement:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as e:
        raise UnsupportedStatementFormat(f"CAMT.053-Datei ist kein gültiges XML: {e}") from e

    stmt = _path(root, "BkToCstmrStmt", "Stmt")
    if stmt is None:
        raise UnsupportedStatementFormat("Keine <Stmt>-Struktur gefunden — kein CAMT.053?")

    iban = _text(_path(stmt, "Acct", "Id", "IBAN"))
    if not iban:
        raise UnsupportedStatementFormat("CAMT.053-Datei enthält keine Konto-IBAN")

    def _balance(code: str) -> tuple[Optional[Decimal], Optional[date]]:
        for bal in stmt:
            if _local(bal.tag) != "Bal":
                continue
            if _text(_path(bal, "Tp", "CdOrPrtry", "Cd")) != code:
                continue
            amt_elem = _child(bal, "Amt")
            if amt_elem is None or amt_elem.text is None:
                continue
            amount = Decimal(amt_elem.text.strip())
            if _text(_child(bal, "CdtDbtInd")) == "DBIT":
                amount = -amount
            date_text = _text(_path(bal, "Dt", "Dt")) or _text(_path(bal, "Dt", "DtTm"))
            return amount, (date.fromisoformat(date_text[:10]) if date_text else None)
        return None, None

    opening_balance, _ = _balance("OPBD")
    closing_balance, closing_balance_date = _balance("CLBD")

    lines = []
    for entry in stmt:
        if _local(entry.tag) != "Ntry":
            continue

        amt_elem = _child(entry, "Amt")
        if amt_elem is None or amt_elem.text is None:
            continue
        amount = Decimal(amt_elem.text.strip())
        if _text(_child(entry, "CdtDbtInd")) == "DBIT":
            amount = -amount

        booking_date_text = _text(_path(entry, "BookgDt", "Dt")) or _text(_path(entry, "BookgDt", "DtTm"))
        if not booking_date_text:
            continue
        booking_date = date.fromisoformat(booking_date_text[:10])

        tx_dtls = _path(entry, "NtryDtls", "TxDtls")
        purpose_text = counterparty_name = counterparty_iban = bank_reference = None
        if tx_dtls is not None:
            purpose_text = _text(_path(tx_dtls, "RmtInf", "Ustrd"))
            bank_reference = (
                _text(_path(tx_dtls, "Refs", "AcctSvcrRef"))
                or _text(_path(tx_dtls, "Refs", "EndToEndId"))
            )
            rltd_pties = _child(tx_dtls, "RltdPties")
            if rltd_pties is not None:
                # Whichever side isn't "us": Dbtr paid us (credit), Cdtr received from us (debit).
                party_tag, acct_tag = ("Dbtr", "DbtrAcct") if amount >= 0 else ("Cdtr", "CdtrAcct")
                counterparty_name = _text(_path(rltd_pties, party_tag, "Nm"))
                counterparty_iban = _text(_path(rltd_pties, acct_tag, "Id", "IBAN"))
        if not bank_reference:
            bank_reference = _text(_child(entry, "AcctSvcrRef")) or _text(_child(entry, "NtryRef"))

        lines.append(ParsedStatementLine(
            booking_date=booking_date,
            amount=amount,
            purpose_text=purpose_text,
            counterparty_name=counterparty_name,
            counterparty_iban=counterparty_iban,
            bank_reference=bank_reference,
        ))

    return ParsedStatement(
        iban=iban, lines=lines,
        opening_balance=opening_balance, closing_balance=closing_balance,
        closing_balance_date=closing_balance_date,
    )


# --- CSV import ------------------------------------------------------------
#
# Unlike MT940/CAMT.053, CSV bank exports have no standard schema at all —
# column names, delimiter, decimal separator and date format all vary by
# bank (confirmed against a real export, 2026-09). So instead of one fixed
# parser, the treasurer maps columns once per account (remembered on
# BankAccount.csv_mapping for next time) via a two-step flow: preview_csv()
# shows the file's columns + a few sample rows and a best-effort guess at
# the mapping (by column name, only ever a starting point — never trusted
# without the treasurer seeing the sample rows first), then
# parse_csv_statement() does the actual import once the mapping is confirmed.
# Parsed with stdlib `csv` (DictReader) — no pandas or similar; a bank CSV
# export is exactly the shape stdlib csv already handles well.

@dataclass
class CsvColumnMapping:
    delimiter: str = ";"
    decimal_separator: str = ","  # "," (1.234,56) or "." (1,234.56)
    date_format: str = "%d.%m.%Y"
    booking_date_column: str = ""
    amount_column: str = ""
    purpose_column: Optional[str] = None
    counterparty_name_column: Optional[str] = None
    counterparty_iban_column: Optional[str] = None
    bank_reference_column: Optional[str] = None
    own_iban_column: Optional[str] = None  # for the account-match safety check
    balance_after_column: Optional[str] = None  # running balance -> reconciliation


# (field name on CsvColumnMapping, [name substrings to look for, most specific first])
_COLUMN_GUESS_PATTERNS: list[tuple[str, list[str]]] = [
    ("own_iban_column", ["iban auftragskonto", "iban eigenes konto"]),
    ("booking_date_column", ["buchungstag", "buchungsdatum"]),
    ("amount_column", ["betrag"]),
    ("balance_after_column", ["saldo nach buchung", "saldo"]),
    ("counterparty_iban_column", ["iban zahlungsbeteiligter", "iban gegenkonto", "iban"]),
    ("counterparty_name_column", ["name zahlungsbeteiligter", "empfänger", "auftraggeber", "beguenstigter"]),
    # Deliberately NOT "mandatsreferenz"/"gläubiger id": those identify a
    # recurring SEPA mandate/creditor, not a single transaction — guessing
    # them here would make repeat payments under the same mandate collide
    # and get wrongly deduped against each other. Only guess an explicit
    # per-transaction reference field.
    ("bank_reference_column", ["end-to-end", "endtoend", "referenznummer"]),
    ("purpose_column", ["verwendungszweck"]),
]


def guess_csv_mapping(columns: list[str]) -> dict[str, Optional[str]]:
    """Best-effort column-name guess, in priority order so a generic "iban"
    match doesn't steal the column a more specific pattern (e.g. "iban
    auftragskonto") should claim. Always shown to the treasurer as a
    pre-filled but editable starting point — never applied unseen."""
    guess: dict[str, Optional[str]] = {}
    claimed: set[str] = set()
    for field, patterns in _COLUMN_GUESS_PATTERNS:
        guess[field] = None
        for pattern in patterns:
            match = next(
                (c for c in columns if c not in claimed and pattern in c.lower()), None
            )
            if match:
                guess[field] = match
                claimed.add(match)
                break
    return guess


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def sniff_csv_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=";,\t|").delimiter
    except csv.Error:
        return ";"  # most common for German bank exports


def preview_csv(content: bytes, delimiter: Optional[str] = None, sample_rows: int = 5):
    """Returns (delimiter_used, columns, sample_rows) — no semantic parsing
    yet, just enough for the treasurer to see what they're mapping."""
    text = _decode_csv(content)
    delimiter = delimiter or sniff_csv_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows or not rows[0]:
        raise UnsupportedStatementFormat("CSV-Datei ist leer oder hat keine Kopfzeile")
    columns, data_rows = rows[0], rows[1:]
    return delimiter, columns, data_rows[:sample_rows]


def _parse_csv_amount(raw: str, decimal_separator: str) -> Decimal:
    raw = raw.strip().replace(" ", "")
    if decimal_separator == ",":
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    return Decimal(raw)


def parse_csv_statement(content: bytes, mapping: CsvColumnMapping) -> ParsedStatement:
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text), delimiter=mapping.delimiter)
    if not reader.fieldnames:
        raise UnsupportedStatementFormat("CSV-Datei enthält keine Kopfzeile")

    missing = [
        c for c in (mapping.booking_date_column, mapping.amount_column)
        if c not in reader.fieldnames
    ]
    if missing:
        raise UnsupportedStatementFormat(f"Spalte(n) nicht in der CSV-Datei gefunden: {', '.join(missing)}")

    def _optional(row: dict, column: Optional[str]) -> Optional[str]:
        if not column:
            return None
        value = (row.get(column) or "").strip()
        return value or None

    lines = []
    own_iban = None
    closing_balance = None
    closing_balance_date = None
    for row in reader:
        raw_date = (row.get(mapping.booking_date_column) or "").strip()
        raw_amount = (row.get(mapping.amount_column) or "").strip()
        if not raw_date or not raw_amount:
            continue  # blank line or a trailing footer row some exports add

        try:
            booking_date = datetime.strptime(raw_date, mapping.date_format).date()
        except ValueError as e:
            raise UnsupportedStatementFormat(
                f"Datum '{raw_date}' passt nicht zum Format '{mapping.date_format}'"
            ) from e
        try:
            amount = _parse_csv_amount(raw_amount, mapping.decimal_separator)
        except InvalidOperation as e:
            raise UnsupportedStatementFormat(f"Betrag '{raw_amount}' konnte nicht gelesen werden") from e

        lines.append(ParsedStatementLine(
            booking_date=booking_date,
            amount=amount,
            purpose_text=_optional(row, mapping.purpose_column),
            counterparty_name=_optional(row, mapping.counterparty_name_column),
            counterparty_iban=_optional(row, mapping.counterparty_iban_column),
            bank_reference=_optional(row, mapping.bank_reference_column),
        ))

        if own_iban is None:
            own_iban = _optional(row, mapping.own_iban_column)
        balance_after = _optional(row, mapping.balance_after_column)
        if balance_after:
            try:
                closing_balance = _parse_csv_amount(balance_after, mapping.decimal_separator)
                closing_balance_date = booking_date
            except InvalidOperation:
                pass  # reconciliation is best-effort — don't fail the whole import over it

    if not lines:
        raise UnsupportedStatementFormat("Keine verwertbaren Zeilen in der CSV-Datei gefunden")

    return ParsedStatement(
        iban=own_iban or "",
        lines=lines,
        closing_balance=closing_balance,
        closing_balance_date=closing_balance_date,
    )
