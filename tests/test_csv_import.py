from decimal import Decimal
from pathlib import Path

import pytest

from app.models.ledger import BankAccount, LedgerCategory, LedgerCategoryKind, LedgerSphere

_FIXTURES = Path(__file__).parent / "fixtures"
_CSV_SAMPLE = (_FIXTURES / "sample.csv").read_bytes()


@pytest.fixture
def bank_account(db):
    a = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("0.00"))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def income_category(db):
    c = LedgerCategory(
        name="Mitgliedsbeiträge", slug="mitgliedsbeitraege",
        kind=LedgerCategoryKind.income, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(c)
    db.commit()
    return c


# The guessed mapping for sample.csv's real-world header — reused across tests
# instead of re-guessing, since guess_csv_mapping() itself has its own unit
# tests in test_bank_statement.py.
_MAPPING_PARAMS = (
    "delimiter=%3B"
    "&booking_date_column=Buchungstag"
    "&amount_column=Betrag"
    "&purpose_column=Verwendungszweck"
    "&counterparty_name_column=Name+Zahlungsbeteiligter"
    "&counterparty_iban_column=IBAN+Zahlungsbeteiligter"
    "&own_iban_column=IBAN+Auftragskonto"
    "&balance_after_column=Saldo+nach+Buchung"
)


def _import_csv(client, account_id, content=_CSV_SAMPLE, filename="sample.csv", extra_params=""):
    return client.post(
        f"/api/v1/ledger/import/csv?bank_account_id={account_id}&filename={filename}&{_MAPPING_PARAMS}{extra_params}",
        content=content,
        headers={"Content-Type": "text/csv"},
    )


# ---------------------------------------------------------------------------
# POST /ledger/import/csv/preview
# ---------------------------------------------------------------------------

def test_preview_csv_returns_columns_and_guess(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/import/csv/preview", content=_CSV_SAMPLE, headers={"Content-Type": "text/csv"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["delimiter"] == ";"
    assert "Buchungstag" in data["columns"]
    assert len(data["sample_rows"]) == 2
    assert data["guessed_mapping"]["booking_date_column"] == "Buchungstag"
    assert data["guessed_mapping"]["amount_column"] == "Betrag"
    # Mandatsreferenz/Gläubiger-ID must NOT be guessed as bank_reference —
    # they identify a recurring mandate/creditor, not one transaction.
    assert data["guessed_mapping"]["bank_reference_column"] is None


def test_preview_csv_requires_auth(client):
    resp = client.post(
        "/api/v1/ledger/import/csv/preview", content=_CSV_SAMPLE, headers={"Content-Type": "text/csv"},
    )
    assert resp.status_code == 401


def test_preview_empty_csv_rejected(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/import/csv/preview", content=b"", headers={"Content-Type": "text/csv"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /ledger/import/csv
# ---------------------------------------------------------------------------

def test_import_csv_with_mapping_creates_new_lines(treasurer_client, bank_account):
    resp = _import_csv(treasurer_client, bank_account.id)
    assert resp.status_code == 201
    data = resp.json()
    assert data["new_count"] == 2
    assert data["duplicate_count"] == 0


def test_import_csv_parses_german_decimal_and_purpose(treasurer_client, bank_account):
    _import_csv(treasurer_client, bank_account.id)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit = next(l for l in lines if Decimal(str(l["amount"])) > 0)
    assert Decimal(str(credit["amount"])) == Decimal("50.00")
    assert credit["purpose_text"] == "Mitgliedsbeitrag 2026"
    assert credit["counterparty_name"] == "Max Mustermann"


def test_import_csv_captures_running_balance_as_closing_balance(treasurer_client, bank_account):
    _import_csv(treasurer_client, bank_account.id)
    resp = treasurer_client.get(f"/api/v1/ledger/accounts/{bank_account.id}/balance")
    data = resp.json()
    # Last row's "Saldo nach Buchung" = 1020,00 on 02.03.2026.
    assert data["last_statement_balance"] == "1020.00"
    assert data["last_statement_balance_date"] == "2026-03-02"


def test_import_csv_remembers_mapping_on_account(treasurer_client, bank_account):
    _import_csv(treasurer_client, bank_account.id)
    resp = treasurer_client.get("/api/v1/ledger/accounts")
    account = next(a for a in resp.json() if a["id"] == bank_account.id)
    assert account["csv_mapping"]["booking_date_column"] == "Buchungstag"
    assert account["csv_mapping"]["delimiter"] == ";"


def test_import_csv_deduplicates_on_reimport(treasurer_client, bank_account):
    first = _import_csv(treasurer_client, bank_account.id)
    assert first.json()["new_count"] == 2

    second = _import_csv(treasurer_client, bank_account.id)
    data = second.json()
    assert data["new_count"] == 0
    assert data["duplicate_count"] == 2


def test_import_csv_own_iban_mismatch_rejected(treasurer_client, db):
    other = BankAccount(iban="DE00999999990000000000", name="Anderes Konto")
    db.add(other)
    db.commit()
    resp = _import_csv(treasurer_client, other.id)
    assert resp.status_code == 400


def test_import_csv_without_own_iban_column_skips_match_check(treasurer_client, bank_account):
    # Many CSV exports don't carry the account's own IBAN at all — the safety
    # check should be skipped entirely rather than failing closed, unlike
    # MT940/CAMT.053 where the field is mandatory.
    resp = treasurer_client.post(
        f"/api/v1/ledger/import/csv?bank_account_id={bank_account.id}&filename=sample.csv"
        "&delimiter=%3B&booking_date_column=Buchungstag&amount_column=Betrag",
        content=_CSV_SAMPLE, headers={"Content-Type": "text/csv"},
    )
    assert resp.status_code == 201


def test_import_csv_unknown_account_404(treasurer_client):
    resp = _import_csv(treasurer_client, 999)
    assert resp.status_code == 404


def test_import_csv_offline_account_rejected(treasurer_client):
    offline = treasurer_client.post(
        "/api/v1/ledger/accounts", json={"name": "Domain-Guthaben", "is_offline": True},
    ).json()
    resp = _import_csv(treasurer_client, offline["id"])
    assert resp.status_code == 400


def test_import_csv_bad_date_format_rejected(treasurer_client, bank_account):
    resp = treasurer_client.post(
        f"/api/v1/ledger/import/csv?bank_account_id={bank_account.id}&filename=sample.csv"
        "&delimiter=%3B&date_format=%25Y-%25m-%25d&booking_date_column=Buchungstag&amount_column=Betrag",
        content=_CSV_SAMPLE, headers={"Content-Type": "text/csv"},
    )
    assert resp.status_code == 400


def test_import_csv_missing_column_rejected(treasurer_client, bank_account):
    resp = treasurer_client.post(
        f"/api/v1/ledger/import/csv?bank_account_id={bank_account.id}&filename=sample.csv"
        "&delimiter=%3B&booking_date_column=Nicht+Vorhanden&amount_column=Betrag",
        content=_CSV_SAMPLE, headers={"Content-Type": "text/csv"},
    )
    assert resp.status_code == 400


def test_book_csv_import_line(treasurer_client, bank_account, income_category):
    _import_csv(treasurer_client, bank_account.id)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit = next(l for l in lines if Decimal(str(l["amount"])) > 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit['id']}/book",
        json={
            "description": "Mitgliedsbeitrag Max Mustermann",
            "category_lines": [{"category_id": income_category.id, "amount": "-50.00"}],
        },
    )
    assert resp.status_code == 201


def test_auditor_cannot_import_csv(auditor_client, bank_account):
    resp = _import_csv(auditor_client, bank_account.id)
    assert resp.status_code in (401, 403)
