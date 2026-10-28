"""Receipt markers on expense bookings and the draft/closed workflow of the
Kassenprüfungsprotokoll (stats, finalize, lock, supplement, PDF)."""
from decimal import Decimal

import pytest

from app.models.ledger import BankAccount, LedgerCategory, LedgerCategoryKind, LedgerSphere


@pytest.fixture
def bank_account(db):
    a = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("0.00"))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def expense_category(db):
    c = LedgerCategory(name="Wareneinkauf", slug="wareneinkauf", kind=LedgerCategoryKind.expense,
                       sphere=LedgerSphere.zweckbetrieb, active=True)
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def income_category(db):
    c = LedgerCategory(name="Mitgliedsbeiträge", slug="beitraege", kind=LedgerCategoryKind.income,
                       sphere=LedgerSphere.ideell, active=True)
    db.add(c)
    db.commit()
    return c


def _book(client, bank_account, category, amount, *, expense=True, entry_date="2025-03-01", doc=None):
    sign = -1 if expense else 1
    cat_line = {"category_id": category.id, "amount": f"{-sign * Decimal(amount):.2f}"}
    if doc:
        cat_line["paperless_document_id"] = doc
    resp = client.post("/api/v1/ledger/entries", json={
        "entry_date": entry_date, "description": "Buchung",
        "lines": [{"bank_account_id": bank_account.id, "amount": f"{sign * Decimal(amount):.2f}"}, cat_line],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _entry(client, entry_id):
    rows = client.get("/api/v1/ledger/entries", params={"ids": entry_id}).json()
    return rows[0]


def _report(**overrides):
    body = {"period_start": "2025-01-01", "period_end": "2025-12-31", "audit_date": "2026-03-01",
            "auditors": "Max Mustermann", "receipts_complete": True, "statements_complete": True}
    body.update(overrides)
    return body


# --- receipt markers ---

def test_expense_without_document_is_missing_and_filterable(treasurer_client, bank_account, expense_category, income_category):
    missing = _book(treasurer_client, bank_account, expense_category, "20.00")
    _book(treasurer_client, bank_account, expense_category, "30.00", doc="42")
    _book(treasurer_client, bank_account, income_category, "50.00", expense=False)

    assert _entry(treasurer_client, missing["id"])["receipt_missing"] is True
    rows = treasurer_client.get("/api/v1/ledger/entries", params={"receipt_missing": "true"}).json()
    assert [r["id"] for r in rows] == [missing["id"]]


def test_receipt_marker_clears_missing(treasurer_client, bank_account, expense_category):
    e = _book(treasurer_client, bank_account, expense_category, "20.00")
    resp = treasurer_client.put(f"/api/v1/ledger/entries/{e['id']}/receipt", json={"status": "external", "note": "Ordner 3, Blatt 12"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["receipt_status"] == "external" and body["receipt_note"] == "Ordner 3, Blatt 12"
    assert body["receipt_missing"] is False

    resp = treasurer_client.put(f"/api/v1/ledger/entries/{e['id']}/receipt", json={"status": None})
    assert resp.json()["receipt_missing"] is True


def test_external_marker_needs_note_not_required_does_not(treasurer_client, bank_account, expense_category):
    e = _book(treasurer_client, bank_account, expense_category, "1.00")
    assert treasurer_client.put(f"/api/v1/ledger/entries/{e['id']}/receipt", json={"status": "external"}).status_code == 400
    resp = treasurer_client.put(f"/api/v1/ledger/entries/{e['id']}/receipt", json={"status": "not_required"})
    assert resp.status_code == 200 and resp.json()["receipt_missing"] is False


def test_reversed_entry_and_storno_never_missing(treasurer_client, bank_account, expense_category):
    e = _book(treasurer_client, bank_account, expense_category, "20.00")
    storno = treasurer_client.post(f"/api/v1/ledger/entries/{e['id']}/reverse").json()
    assert _entry(treasurer_client, e["id"])["receipt_missing"] is False
    assert _entry(treasurer_client, storno["id"])["receipt_missing"] is False


def test_auditor_may_mark_receipt_plain_viewer_may_not(auditor_client, treasurer_client, bank_account, expense_category, client):
    e = _book(treasurer_client, bank_account, expense_category, "20.00")
    assert auditor_client.put(f"/api/v1/ledger/entries/{e['id']}/receipt", json={"status": "not_required"}).status_code == 200
    assert client.put(f"/api/v1/ledger/entries/{e['id']}/receipt", json={"status": "not_required"}).status_code in (401, 403)


def test_refund_booked_against_income_category_needs_receipt(treasurer_client, bank_account, income_category):
    refund = _book(treasurer_client, bank_account, income_category, "15.00", expense=True)
    income = _book(treasurer_client, bank_account, income_category, "15.00", expense=False)
    assert _entry(treasurer_client, refund["id"])["receipt_missing"] is True
    assert _entry(treasurer_client, income["id"])["receipt_missing"] is False


# --- stats ---

def test_period_stats(treasurer_client, auditor_client, bank_account, expense_category):
    a = _book(treasurer_client, bank_account, expense_category, "10.00")
    b = _book(treasurer_client, bank_account, expense_category, "10.00")
    _book(treasurer_client, bank_account, expense_category, "10.00", entry_date="2024-05-01")  # outside period
    auditor_client.post(f"/api/v1/ledger/entries/{a['id']}/review")
    treasurer_client.put(f"/api/v1/ledger/entries/{b['id']}/receipt", json={"status": "not_required"})

    st = treasurer_client.get("/api/v1/ledger/audit-period-stats", params={"period_start": "2025-01-01", "period_end": "2025-12-31"}).json()
    assert (st["entries_total"], st["entries_reviewed"], st["entries_unreviewed"]) == (2, 1, 1)
    assert (st["expense_entries"], st["receipts_missing"], st["receipts_not_required"]) == (2, 1, 1)


# --- report workflow ---

def test_finalize_freezes_stats_and_locks_report(auditor_client, treasurer_client, bank_account, expense_category):
    e = _book(treasurer_client, bank_account, expense_category, "10.00")
    rep = auditor_client.post("/api/v1/ledger/audit-reports", json=_report()).json()
    assert rep["status"] == "draft" and rep["stats"]["entries_reviewed"] == 0

    resp = auditor_client.post(f"/api/v1/ledger/audit-reports/{rep['id']}/finalize")
    assert resp.status_code == 200
    final = resp.json()
    assert final["status"] == "final" and final["finalized_by"] and final["stats"]["entries_unreviewed"] == 1

    # later activity changes the live figures, not the frozen ones
    other = _book(treasurer_client, bank_account, expense_category, "5.00", entry_date="2025-04-01")
    auditor_client.post(f"/api/v1/ledger/entries/{other['id']}/review")
    shown = next(r for r in auditor_client.get("/api/v1/ledger/audit-reports").json() if r["id"] == rep["id"])
    assert shown["stats"]["entries_total"] == 1
    assert shown["current_stats"]["entries_total"] == 2 and shown["current_stats"]["entries_reviewed"] == 1

    assert auditor_client.put(f"/api/v1/ledger/audit-reports/{rep['id']}", json={"findings": "x"}).status_code == 409
    assert auditor_client.delete(f"/api/v1/ledger/audit-reports/{rep['id']}").status_code == 409
    assert auditor_client.post(f"/api/v1/ledger/audit-reports/{rep['id']}/finalize").status_code == 409
    assert e["id"]


def test_finalize_requires_receipt_and_statement_choice(auditor_client):
    rep = auditor_client.post("/api/v1/ledger/audit-reports", json=_report(receipts_complete=None, statements_complete=None)).json()
    assert auditor_client.post(f"/api/v1/ledger/audit-reports/{rep['id']}/finalize").status_code == 400


def test_check_mark_locked_inside_closed_period(auditor_client, treasurer_client, bank_account, expense_category):
    inside = _book(treasurer_client, bank_account, expense_category, "10.00", entry_date="2025-06-01")
    outside = _book(treasurer_client, bank_account, expense_category, "10.00", entry_date="2026-02-01")
    for e in (inside, outside):
        auditor_client.post(f"/api/v1/ledger/entries/{e['id']}/review")
    rep = auditor_client.post("/api/v1/ledger/audit-reports", json=_report()).json()
    # still possible while the report is a draft
    assert auditor_client.delete(f"/api/v1/ledger/entries/{outside['id']}/review").status_code == 200
    auditor_client.post(f"/api/v1/ledger/entries/{outside['id']}/review")
    auditor_client.post(f"/api/v1/ledger/audit-reports/{rep['id']}/finalize")

    assert auditor_client.delete(f"/api/v1/ledger/entries/{inside['id']}/review").status_code == 409
    assert auditor_client.delete(f"/api/v1/ledger/entries/{outside['id']}/review").status_code == 200


def test_supplement_needs_closed_report(auditor_client):
    draft = auditor_client.post("/api/v1/ledger/audit-reports", json=_report()).json()
    resp = auditor_client.post("/api/v1/ledger/audit-reports", json=_report(supplements_report_id=draft["id"]))
    assert resp.status_code == 409

    auditor_client.post(f"/api/v1/ledger/audit-reports/{draft['id']}/finalize")
    resp = auditor_client.post("/api/v1/ledger/audit-reports", json=_report(supplements_report_id=draft["id"], findings="Nachtrag"))
    assert resp.status_code == 201
    assert resp.json()["supplements_report_id"] == draft["id"] and resp.json()["status"] == "draft"


def test_pdf_for_draft_and_closed_report(auditor_client, treasurer_client):
    rep = auditor_client.post("/api/v1/ledger/audit-reports", json=_report(place="Hildesheim", remarks="keine")).json()
    resp = treasurer_client.get(f"/api/v1/ledger/audit-reports/{rep['id']}/pdf?lang=de")
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    assert "entwurf" in resp.headers["content-disposition"]

    auditor_client.post(f"/api/v1/ledger/audit-reports/{rep['id']}/finalize")
    resp = treasurer_client.get(f"/api/v1/ledger/audit-reports/{rep['id']}/pdf?lang=en")
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    assert "entwurf" not in resp.headers["content-disposition"]


def test_closed_account_exempt_from_stale_check(treasurer_client, auditor_client):
    acc = treasurer_client.post(
        "/api/v1/ledger/accounts", json={"iban": "DE02120300000000202051", "name": "Altes Konto"}
    ).json()
    url = "/api/v1/ledger/audit-period-stats?period_start=2025-01-01&period_end=2025-12-31"
    assert treasurer_client.get(url).json()["accounts_stale"] == 1

    resp = treasurer_client.put(f"/api/v1/ledger/accounts/{acc['id']}", json={"closed_on": "2024-06-30"})
    assert resp.status_code == 200
    assert resp.json()["closed_on"] == "2024-06-30"
    assert treasurer_client.get(url).json()["accounts_stale"] == 0
    # A period ending before the closing date still expects statements.
    early = "/api/v1/ledger/audit-period-stats?period_start=2024-01-01&period_end=2024-03-31"
    assert treasurer_client.get(early).json()["accounts_stale"] == 1

    resp = treasurer_client.put(f"/api/v1/ledger/accounts/{acc['id']}", json={"clear_closed_on": True})
    assert resp.json()["closed_on"] is None
    assert treasurer_client.get(url).json()["accounts_stale"] == 1
