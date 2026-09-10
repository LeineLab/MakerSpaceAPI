"""Parse bank statement exports (MT940, CAMT.053) into a common shape.

Both formats vary across banks (SEPA vs. legacy field codes, schema versions),
so extraction of everything except date/amount/purpose is best-effort — the
raw purpose text is always preserved so nothing is lost for manual review.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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

    return ParsedStatement(iban=iban, lines=lines_from_mt940_transactions(statement))


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

    return ParsedStatement(iban=iban, lines=lines)
