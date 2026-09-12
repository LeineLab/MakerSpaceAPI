from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.models.ledger import (
    BankAccount,
    LedgerCategory,
    LedgerCategoryKind,
    LedgerImportBatch,
    LedgerImportLine,
    LedgerImportSource,
    LedgerImportStatus,
    LedgerSphere,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_MT940_SAMPLE = (_FIXTURES / "sample.sta").read_bytes()
_MT940_LEGACY_BLZ_SAMPLE = (_FIXTURES / "sample_legacy_blz.sta").read_bytes()
_CAMT053_SAMPLE = (_FIXTURES / "sample_camt053.xml").read_bytes()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bank_account(db):
    a = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("0.00"))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def untracked_account(db):
    a = BankAccount(
        iban="DE89370400440532013000", name="Privatkonto",
        opening_balance=Decimal("0.00"), tracked=False,
    )
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


@pytest.fixture
def expense_category(db):
    c = LedgerCategory(
        name="Wareneinkauf", slug="wareneinkauf",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.zweckbetrieb, active=True,
    )
    db.add(c)
    db.commit()
    return c


def _upload(client, account_id, content, filename="sample.sta"):
    return client.post(
        f"/api/v1/ledger/import/file?bank_account_id={account_id}&filename={filename}",
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


# ---------------------------------------------------------------------------
# POST /ledger/import/file
# ---------------------------------------------------------------------------

def test_import_requires_auth(client, bank_account):
    resp = _upload(client, bank_account.id, _MT940_SAMPLE)
    assert resp.status_code == 401


def test_import_mt940_creates_new_lines(treasurer_client, bank_account):
    resp = _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    assert resp.status_code == 201
    data = resp.json()
    assert data["new_count"] == 2
    assert data["duplicate_count"] == 0
    assert data["total_count"] == 2


def test_import_captures_statement_balances_for_reconciliation(treasurer_client, bank_account):
    # sample.sta's :60F:/:62F: fields: opening 1000.00 @ 2026-01-01, closing
    # 1020.00 @ 2026-03-01 — captured on the batch, surfaced via the balance
    # endpoint's last_statement_* fields (not compared automatically at
    # import time, since the just-staged lines aren't booked yet).
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)

    resp = treasurer_client.get(f"/api/v1/ledger/accounts/{bank_account.id}/balance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["last_statement_balance"] == "1020.00"
    assert data["last_statement_balance_date"] == "2026-03-01"


def test_reconciliation_mismatch_then_match_after_booking(treasurer_client, income_category, db):
    # sample.sta: opening 1000.00 @ 2026-01-01, closing 1020.00 @ 2026-03-01;
    # lines are +50.00 (2026-03-01) and -30.00 (2026-03-02, AFTER the closing
    # date — correctly irrelevant to reconciliation as of 2026-03-01).
    # opening_balance=970.00 so 970 + 50 == 1020 once the +50 line is booked.
    account = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("970.00"))
    db.add(account)
    db.commit()

    _upload(treasurer_client, account.id, _MT940_SAMPLE)

    # Nothing booked yet — the staged lines don't count toward the computed balance.
    before = treasurer_client.get(f"/api/v1/ledger/accounts/{account.id}/balance").json()
    assert before["balance_as_of_last_statement"] == "970.00"
    assert before["last_statement_balance"] == "1020.00"

    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit_line = next(l for l in lines if Decimal(str(l["amount"])) > 0)
    treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit_line['id']}/book",
        json={
            "description": "Mitgliedsbeitrag",
            "category_lines": [{"category_id": income_category.id, "amount": "-50.00"}],
        },
    )

    after = treasurer_client.get(f"/api/v1/ledger/accounts/{account.id}/balance").json()
    assert after["balance_as_of_last_statement"] == "1020.00"
    assert after["last_statement_balance"] == "1020.00"


def test_import_camt053_captures_statement_balances(treasurer_client, bank_account):
    resp_upload = _upload(treasurer_client, bank_account.id, _CAMT053_SAMPLE, filename="sample.xml")
    assert resp_upload.status_code == 201

    resp = treasurer_client.get(f"/api/v1/ledger/accounts/{bank_account.id}/balance")
    data = resp.json()
    assert data["last_statement_balance"] == "1020.00"
    assert data["last_statement_balance_date"] == "2026-03-02"


def test_import_mt940_legacy_blz_format_matches_account(treasurer_client, bank_account):
    # bank_account's IBAN is DE02120300000000202051 — this fixture's :25:
    # field is the pre-SEPA "12030000/0000202051" (BLZ/Kontonummer) form,
    # which some banks still export instead of an IBAN (real-world case).
    resp = _upload(treasurer_client, bank_account.id, _MT940_LEGACY_BLZ_SAMPLE)
    assert resp.status_code == 201
    assert resp.json()["new_count"] == 2


def test_import_camt053_creates_new_lines(treasurer_client, bank_account):
    resp = _upload(treasurer_client, bank_account.id, _CAMT053_SAMPLE, filename="sample.xml")
    assert resp.status_code == 201
    data = resp.json()
    assert data["new_count"] == 2
    assert data["duplicate_count"] == 0


def test_reimporting_same_file_is_fully_deduped_by_reference(treasurer_client, bank_account):
    first = _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    assert first.json()["new_count"] == 2

    second = _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    data = second.json()
    assert data["new_count"] == 0
    assert data["duplicate_count"] == 2


def test_duplicate_flagged_line_can_still_be_booked(treasurer_client, bank_account, income_category):
    # A duplicate flag is a heuristic, not a hard rejection — the treasurer
    # must be able to override it (e.g. a genuinely new transaction that
    # happens to collide with an existing dedup_hash).
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    duplicates = treasurer_client.get("/api/v1/ledger/import/lines?status=duplicate").json()
    assert len(duplicates) == 2
    credit_line = next(l for l in duplicates if Decimal(str(l["amount"])) > 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit_line['id']}/book",
        json={
            "description": "Doch kein Duplikat",
            "category_lines": [{"category_id": income_category.id, "amount": "-50.00"}],
        },
    )
    assert resp.status_code == 201

    updated = treasurer_client.get("/api/v1/ledger/import/lines?status=booked").json()
    assert any(l["id"] == credit_line["id"] for l in updated)


def test_duplicate_flagged_line_can_be_ignored(treasurer_client, bank_account):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    duplicates = treasurer_client.get("/api/v1/ledger/import/lines?status=duplicate").json()

    resp = treasurer_client.post(f"/api/v1/ledger/import/lines/{duplicates[0]['id']}/ignore")
    assert resp.status_code == 200

    remaining_duplicates = treasurer_client.get("/api/v1/ledger/import/lines?status=duplicate").json()
    assert len(remaining_duplicates) == 1


def test_import_unknown_account_404(treasurer_client):
    resp = _upload(treasurer_client, 999, _MT940_SAMPLE)
    assert resp.status_code == 404


def test_import_untracked_account_rejected(treasurer_client, untracked_account):
    # untracked_account's IBAN doesn't match the fixture, but tracked=False
    # must be rejected before the file is even parsed.
    resp = _upload(treasurer_client, untracked_account.id, _MT940_SAMPLE)
    assert resp.status_code == 400


def test_import_offline_account_rejected(treasurer_client):
    offline = treasurer_client.post(
        "/api/v1/ledger/accounts", json={"name": "Domain-Guthaben", "is_offline": True},
    ).json()
    resp = _upload(treasurer_client, offline["id"], _MT940_SAMPLE)
    assert resp.status_code == 400


def test_import_iban_mismatch_rejected(treasurer_client, db):
    other = BankAccount(iban="DE00999999990000000000", name="Anderes Konto")
    db.add(other)
    db.commit()
    resp = _upload(treasurer_client, other.id, _MT940_SAMPLE)
    assert resp.status_code == 400


def test_import_unsupported_format_rejected(treasurer_client, bank_account):
    resp = _upload(treasurer_client, bank_account.id, b"this is not a bank statement")
    assert resp.status_code == 400


def test_dedup_hash_fallback_distinguishes_genuine_repeats(treasurer_client, bank_account):
    """Two lines with identical date/amount/purpose but no bank reference: the
    first import must treat both as new (real, distinct transactions); a
    second import of the same file must treat both as duplicates — not just
    one — via the multiset count, not a plain seen/unseen set."""
    statement = (
        b":20:STARTUMSE\n"
        b":25:DE02120300000000202051\n"
        b":28C:1/1\n"
        b":60F:C260101EUR1000,00\n"
        b":61:2603010301C25,00NTRFNONREF\n"
        b":86:166?00Spende\n"
        b":61:2603010301C25,00NTRFNONREF\n"
        b":86:166?00Spende\n"
        b":62F:C260301EUR1050,00\n"
    )
    first = _upload(treasurer_client, bank_account.id, statement)
    assert first.json() == {
        "batch_id": first.json()["batch_id"], "new_count": 2, "duplicate_count": 0, "total_count": 2,
    }

    second = _upload(treasurer_client, bank_account.id, statement)
    data = second.json()
    assert data["new_count"] == 0
    assert data["duplicate_count"] == 2


def test_shared_placeholder_reference_does_not_cross_flag_different_amounts(treasurer_client, bank_account):
    """Real-world bug (2026-09-11): a bank fills in the SEPA placeholder
    NOTPROVIDED for any payment without an explicit end-to-end reference, so
    many genuinely distinct transactions share that same "reference". Two
    different amounts sharing it must both be `new`, not just the first."""
    statement = (
        b":20:STARTUMSE\n"
        b":25:DE02120300000000202051\n"
        b":28C:1/1\n"
        b":60F:C260101EUR1000,00\n"
        b":61:2603010301C25,00NTRFNOTPROVIDED\n"
        b":86:166?00Spende A\n"
        b":61:2603020301C40,00NTRFNOTPROVIDED\n"
        b":86:166?00Spende B\n"
        b":62F:C260301EUR1065,00\n"
    )
    resp = _upload(treasurer_client, bank_account.id, statement)
    data = resp.json()
    assert data["new_count"] == 2
    assert data["duplicate_count"] == 0


def test_shared_placeholder_reference_with_same_amount_still_deduped_on_reimport(treasurer_client, bank_account):
    """The flip side: if two lines really do share both the placeholder
    reference *and* the same amount, re-importing the same file must still
    flag both as duplicates via the multiset count — the fix narrows the
    match key, it doesn't disable reference-based dedup."""
    statement = (
        b":20:STARTUMSE\n"
        b":25:DE02120300000000202051\n"
        b":28C:1/1\n"
        b":60F:C260101EUR1000,00\n"
        b":61:2603010301C25,00NTRFNOTPROVIDED\n"
        b":86:166?00Spende A\n"
        b":61:2603020301C25,00NTRFNOTPROVIDED\n"
        b":86:166?00Spende B\n"
        b":62F:C260301EUR1050,00\n"
    )
    first = _upload(treasurer_client, bank_account.id, statement)
    assert first.json()["new_count"] == 2

    second = _upload(treasurer_client, bank_account.id, statement)
    data = second.json()
    assert data["new_count"] == 0
    assert data["duplicate_count"] == 2


# ---------------------------------------------------------------------------
# GET /ledger/import/lines
# ---------------------------------------------------------------------------

def test_list_import_lines_defaults_to_new(auditor_client, treasurer_client, bank_account):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    resp = auditor_client.get("/api/v1/ledger/import/lines")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert all(line["status"] == "new" for line in resp.json())


def test_list_import_lines_reports_total_count_and_pages(treasurer_client, bank_account):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)  # 2 new lines

    resp = treasurer_client.get("/api/v1/ledger/import/lines?limit=1")
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "2"
    assert len(resp.json()) == 1

    second_page = treasurer_client.get("/api/v1/ledger/import/lines?limit=1&offset=1")
    assert second_page.headers["X-Total-Count"] == "2"
    assert len(second_page.json()) == 1
    assert second_page.json()[0]["id"] != resp.json()[0]["id"]


# ---------------------------------------------------------------------------
# suggested_category_id (Key Design Decision #45) — purpose-text autofill
# ---------------------------------------------------------------------------

def test_list_import_lines_suggests_category_from_match_keyword(treasurer_client, bank_account):
    cat = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Mitgliedsbeiträge", "slug": "mitgliedsbeitraege", "kind": "income",
              "sphere": "ideell", "match_keywords": ["Mitgliedsbeitrag"]},
    ).json()

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = {l["amount"]: l for l in treasurer_client.get("/api/v1/ledger/import/lines").json()}

    assert lines["50.00"]["suggested_category_id"] == cat["id"]
    assert lines["-30.00"]["suggested_category_id"] is None


def test_list_import_lines_no_suggestion_without_match_keywords(treasurer_client, bank_account, income_category):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    assert all(l["suggested_category_id"] is None for l in lines)


def test_list_import_lines_no_suggestion_from_inactive_category(treasurer_client, bank_account):
    cat = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Mitgliedsbeiträge", "slug": "mitgliedsbeitraege", "kind": "income",
              "sphere": "ideell", "match_keywords": ["Mitgliedsbeitrag"]},
    ).json()
    treasurer_client.put(f"/api/v1/ledger/categories/{cat['id']}", json={"active": False})

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = {l["amount"]: l for l in treasurer_client.get("/api/v1/ledger/import/lines").json()}
    assert lines["50.00"]["suggested_category_id"] is None


# ---------------------------------------------------------------------------
# POST /ledger/import/lines/{id}/book and /ignore
# ---------------------------------------------------------------------------

def test_book_import_line_success(treasurer_client, bank_account, income_category):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit_line = next(l for l in lines if Decimal(str(l["amount"])) > 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit_line['id']}/book",
        json={
            "description": "Mitgliedsbeitrag Max Mustermann",
            "category_lines": [{"category_id": income_category.id, "amount": "-50.00"}],
        },
    )
    assert resp.status_code == 201
    entry = resp.json()
    assert len(entry["lines"]) == 2


def test_booked_entry_exposes_matched_import_line(treasurer_client, bank_account, income_category):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit_line = next(l for l in lines if Decimal(str(l["amount"])) > 0)
    assert credit_line["purpose_text"]  # sanity: the fixture line actually has one

    entry = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit_line['id']}/book",
        json={
            "description": "Mitgliedsbeitrag Max Mustermann",
            "category_lines": [{"category_id": income_category.id, "amount": "-50.00"}],
        },
    ).json()

    [listed] = [e for e in treasurer_client.get("/api/v1/ledger/entries").json() if e["id"] == entry["id"]]
    [matched] = listed["matched_import_lines"]
    assert matched["id"] == credit_line["id"]
    assert matched["purpose_text"] == credit_line["purpose_text"]
    assert matched["booking_date"] == credit_line["booking_date"]
    assert listed["description"] == "Mitgliedsbeitrag Max Mustermann"


def test_reversing_booked_import_line_reopens_it(treasurer_client, bank_account, income_category):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit_line = next(l for l in lines if Decimal(str(l["amount"])) > 0)

    entry = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit_line['id']}/book",
        json={
            "description": "Mitgliedsbeitrag Max Mustermann",
            "category_lines": [{"category_id": income_category.id, "amount": "-50.00"}],
        },
    ).json()

    booked = treasurer_client.get("/api/v1/ledger/import/lines?status=booked").json()
    assert len(booked) == 1

    resp = treasurer_client.post(f"/api/v1/ledger/entries/{entry['id']}/reverse")
    assert resp.status_code == 201

    reopened = treasurer_client.get("/api/v1/ledger/import/lines?status=new").json()
    assert any(l["id"] == credit_line["id"] and l["matched_entry_id"] is None for l in reopened)
    assert treasurer_client.get("/api/v1/ledger/import/lines?status=booked").json() == []


def test_book_import_line_splits_across_categories(treasurer_client, bank_account, expense_category, db):
    other = LedgerCategory(
        name="Büromaterial", slug="bueromaterial",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(other)
    db.commit()

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Sammelbeleg",
            "category_lines": [
                {"category_id": expense_category.id, "amount": "20.00"},
                {"category_id": other.id, "amount": "10.00"},
            ],
        },
    )
    assert resp.status_code == 201
    assert len(resp.json()["lines"]) == 3


def test_book_import_line_multiple_invoices_each_link_own_document(
    treasurer_client, bank_account, expense_category, db,
):
    """Key Design Decision #44: several invoices paid in one bank debit each
    link their own paperless_document_id on their own split line."""
    other = LedgerCategory(
        name="Büromaterial", slug="bueromaterial-2",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(other)
    db.commit()

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Sammelabbuchung 2 Rechnungen",
            "category_lines": [
                {"category_id": expense_category.id, "amount": "20.00", "paperless_document_id": "INV-A"},
                {"category_id": other.id, "amount": "10.00", "paperless_document_id": "INV-B"},
            ],
        },
    )
    assert resp.status_code == 201
    cat_lines = {l["category_id"]: l["paperless_document_id"] for l in resp.json()["lines"] if l["category_id"] is not None}
    assert cat_lines[expense_category.id] == "INV-A"
    assert cat_lines[other.id] == "INV-B"


def test_book_import_line_rejects_unbalanced_split(treasurer_client, bank_account, income_category):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit_line = next(l for l in lines if Decimal(str(l["amount"])) > 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit_line['id']}/book",
        json={
            "description": "Falsch",
            "category_lines": [{"category_id": income_category.id, "amount": "-40.00"}],
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Booking an import line as a transfer to another account
# ---------------------------------------------------------------------------

def _stage_counter_line(db, account, amount, booking_date=date(2026, 3, 2)):
    """Simulate the counter-account already having its own staged line for
    the same real-world transfer (as if its statement had been imported
    separately) — status='new', not yet booked."""
    batch = LedgerImportBatch(bank_account_id=account.id, source=LedgerImportSource.file, imported_by="test")
    db.add(batch)
    db.flush()
    line = LedgerImportLine(
        batch_id=batch.id, bank_account_id=account.id, booking_date=booking_date,
        amount=amount, purpose_text="Umbuchung", dedup_hash=f"counter-{account.id}-{amount}",
        status=LedgerImportStatus.new,
    )
    db.add(line)
    db.commit()
    return line


def test_book_import_line_as_unmatched_transfer(treasurer_client, bank_account, db):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    db.add(other)
    db.commit()

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)  # -30.00

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Umbuchung ins Sparkonto",
            "category_lines": [{"bank_account_id": other.id, "amount": "30.00"}],
        },
    )
    assert resp.status_code == 201
    entry = resp.json()
    assert len(entry["lines"]) == 2
    assert any(l["bank_account_id"] == other.id for l in entry["lines"])


def test_book_import_line_as_transfer_links_matched_counter_line(treasurer_client, bank_account, db):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    db.add(other)
    db.commit()
    counter = _stage_counter_line(db, other, Decimal("30.00"))

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)  # -30.00

    candidates = treasurer_client.get(
        f"/api/v1/ledger/import/lines?bank_account_id={other.id}&amount=30.00&status=new"
    ).json()
    assert len(candidates) == 1
    assert candidates[0]["id"] == counter.id

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Umbuchung ins Sparkonto",
            "category_lines": [{"bank_account_id": other.id, "amount": "30.00"}],
            "matched_import_line_id": counter.id,
        },
    )
    assert resp.status_code == 201
    entry = resp.json()

    counter_after = treasurer_client.get(f"/api/v1/ledger/import/lines?bank_account_id={other.id}&status=booked").json()
    assert len(counter_after) == 1
    assert counter_after[0]["id"] == counter.id
    assert counter_after[0]["matched_entry_id"] == entry["id"]


def test_book_import_line_transfer_matched_line_wrong_account_rejected(treasurer_client, bank_account, db):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    third = BankAccount(iban="DE00111111110000000000", name="Drittes Konto")
    db.add_all([other, third])
    db.commit()
    counter = _stage_counter_line(db, third, Decimal("30.00"))  # staged on the WRONG account

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Umbuchung ins Sparkonto",
            "category_lines": [{"bank_account_id": other.id, "amount": "30.00"}],
            "matched_import_line_id": counter.id,
        },
    )
    assert resp.status_code == 400


def test_book_import_line_transfer_matched_line_wrong_amount_rejected(treasurer_client, bank_account, db):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    db.add(other)
    db.commit()
    counter = _stage_counter_line(db, other, Decimal("25.00"))  # doesn't match the 30.00 transfer

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Umbuchung ins Sparkonto",
            "category_lines": [{"bank_account_id": other.id, "amount": "30.00"}],
            "matched_import_line_id": counter.id,
        },
    )
    assert resp.status_code == 400


def test_book_import_line_transfer_requires_exactly_one_of_category_or_account(treasurer_client, bank_account, income_category):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Ungültig",
            "category_lines": [{"category_id": income_category.id, "bank_account_id": 999, "amount": "30.00"}],
        },
    )
    assert resp.status_code == 400


def test_book_import_line_at_most_one_transfer_split(treasurer_client, bank_account, db):
    a = BankAccount(iban="DE00999999990000000000", name="Konto A")
    b = BankAccount(iban="DE00111111110000000000", name="Konto B")
    db.add_all([a, b])
    db.commit()

    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    debit_line = next(l for l in lines if Decimal(str(l["amount"])) < 0)

    resp = treasurer_client.post(
        f"/api/v1/ledger/import/lines/{debit_line['id']}/book",
        json={
            "description": "Zwei Umbuchungsziele",
            "category_lines": [
                {"bank_account_id": a.id, "amount": "15.00"},
                {"bank_account_id": b.id, "amount": "15.00"},
            ],
        },
    )
    assert resp.status_code == 400


def test_ignore_import_line(treasurer_client, bank_account):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    resp = treasurer_client.post(f"/api/v1/ledger/import/lines/{lines[0]['id']}/ignore")
    assert resp.status_code == 200

    remaining = treasurer_client.get("/api/v1/ledger/import/lines").json()
    assert len(remaining) == 1


def test_auditor_cannot_ignore(auditor_client, treasurer_client, bank_account):
    # auditor_client and treasurer_client each carry their own signed JWT
    # cookie, so combining both fixtures in one test genuinely exercises two
    # independent identities (see _role_client in conftest.py) — this test is
    # exactly what used to silently pass for the wrong reason when both
    # fixtures shared one global dependency-override identity.
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    line_id = treasurer_client.get("/api/v1/ledger/import/lines").json()[0]["id"]

    resp = auditor_client.post(f"/api/v1/ledger/import/lines/{line_id}/ignore")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Transfer candidates also include duplicate-flagged lines, and
# POST /import/lines/{id}/reset ("Kein Duplikat" / restore from ignored)
# ---------------------------------------------------------------------------

def test_transfer_candidates_include_duplicate_status_line(treasurer_client, bank_account, db):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    db.add(other)
    db.commit()
    counter = _stage_counter_line(db, other, Decimal("30.00"))
    counter.status = LedgerImportStatus.duplicate
    db.commit()

    candidates = treasurer_client.get(
        f"/api/v1/ledger/import/lines?bank_account_id={other.id}&amount=30.00&status=new&status=duplicate"
    ).json()
    assert len(candidates) == 1
    assert candidates[0]["id"] == counter.id

    # the plain default-status search (used by the main staging queue) still
    # excludes it, unaffected by this change
    default_view = treasurer_client.get(f"/api/v1/ledger/import/lines?bank_account_id={other.id}").json()
    assert default_view == []


def test_reset_duplicate_to_new(treasurer_client, bank_account, db):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    db.add(other)
    db.commit()
    counter = _stage_counter_line(db, other, Decimal("30.00"))
    counter.status = LedgerImportStatus.duplicate
    db.commit()

    resp = treasurer_client.post(f"/api/v1/ledger/import/lines/{counter.id}/reset")
    assert resp.status_code == 200
    assert treasurer_client.get(f"/api/v1/ledger/import/lines?bank_account_id={other.id}").json()[0]["status"] == "new"


def test_reset_ignored_to_new(treasurer_client, bank_account):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    line_id = treasurer_client.get("/api/v1/ledger/import/lines").json()[0]["id"]
    treasurer_client.post(f"/api/v1/ledger/import/lines/{line_id}/ignore")

    resp = treasurer_client.post(f"/api/v1/ledger/import/lines/{line_id}/reset")
    assert resp.status_code == 200
    restored = treasurer_client.get("/api/v1/ledger/import/lines?status=new").json()
    assert any(l["id"] == line_id for l in restored)


def test_reset_new_line_rejected_400(treasurer_client, bank_account):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    line_id = treasurer_client.get("/api/v1/ledger/import/lines").json()[0]["id"]

    resp = treasurer_client.post(f"/api/v1/ledger/import/lines/{line_id}/reset")
    assert resp.status_code == 400


def test_reset_booked_line_rejected_400(treasurer_client, bank_account, income_category):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    credit_line = next(l for l in lines if Decimal(str(l["amount"])) > 0)
    treasurer_client.post(
        f"/api/v1/ledger/import/lines/{credit_line['id']}/book",
        json={
            "description": "Mitgliedsbeitrag",
            "category_lines": [{"category_id": income_category.id, "amount": str(-Decimal(str(credit_line["amount"])))}],
        },
    )

    resp = treasurer_client.post(f"/api/v1/ledger/import/lines/{credit_line['id']}/reset")
    assert resp.status_code == 400


def test_auditor_cannot_reset(auditor_client, treasurer_client, bank_account):
    _upload(treasurer_client, bank_account.id, _MT940_SAMPLE)
    line_id = treasurer_client.get("/api/v1/ledger/import/lines").json()[0]["id"]
    treasurer_client.post(f"/api/v1/ledger/import/lines/{line_id}/ignore")

    resp = auditor_client.post(f"/api/v1/ledger/import/lines/{line_id}/reset")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Paperless search + config flag
# ---------------------------------------------------------------------------

def test_paperless_search_disabled_by_default_returns_empty(auditor_client):
    resp = auditor_client.get("/api/v1/ledger/paperless/search?q=beleg")
    assert resp.status_code == 200
    assert resp.json() == []


def test_ledger_config_reports_paperless_disabled(auditor_client):
    resp = auditor_client.get("/api/v1/ledger/config")
    assert resp.json()["paperless_enabled"] is False


def test_paperless_search_when_configured(auditor_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PAPERLESS_URL", "https://paperless.example.com")
    monkeypatch.setattr(settings, "PAPERLESS_API_TOKEN", "test-token")

    assert auditor_client.get("/api/v1/ledger/config").json()["paperless_enabled"] is True

    fake_response = {"results": [{"id": 42, "title": "Rechnung Baumarkt", "created": "2026-03-01"}]}
    with patch("app.services.paperless.httpx.get") as mock_get:
        mock_get.return_value = httpx.Response(
            200, json=fake_response, request=httpx.Request("GET", "https://paperless.example.com/api/documents/"),
        )
        resp = auditor_client.get("/api/v1/ledger/paperless/search?q=baumarkt")

    assert resp.status_code == 200
    assert resp.json() == [{"id": 42, "title": "Rechnung Baumarkt", "created": "2026-03-01"}]


def test_paperless_search_unreachable_returns_empty(auditor_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PAPERLESS_URL", "https://paperless.example.com")
    monkeypatch.setattr(settings, "PAPERLESS_API_TOKEN", "test-token")

    with patch("app.services.paperless.httpx.get", side_effect=httpx.ConnectError("unreachable")):
        resp = auditor_client.get("/api/v1/ledger/paperless/search?q=baumarkt")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Paperless Belege overview (GET /ledger/paperless/documents)
# ---------------------------------------------------------------------------

def test_paperless_documents_disabled_by_default_returns_empty(auditor_client):
    resp = auditor_client.get("/api/v1/ledger/paperless/documents")
    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"


def test_paperless_documents_marks_unlinked_and_linked(
    auditor_client, treasurer_client, monkeypatch, bank_account, expense_category,
):
    from app.config import settings

    monkeypatch.setattr(settings, "PAPERLESS_URL", "https://paperless.example.com")
    monkeypatch.setattr(settings, "PAPERLESS_API_TOKEN", "test-token")

    entry = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Bueromaterial",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "-20.00"},
                {"category_id": expense_category.id, "amount": "20.00", "paperless_document_id": "42"},
            ],
        },
    ).json()

    fake_response = {
        "count": 2,
        "results": [
            {"id": 42, "title": "Rechnung Bueromarkt", "created": "2026-03-01"},
            {"id": 99, "title": "Rechnung Getraenke", "created": "2026-02-15"},
        ],
    }
    with patch("app.services.paperless.httpx.get") as mock_get:
        mock_get.return_value = httpx.Response(
            200, json=fake_response, request=httpx.Request("GET", "https://paperless.example.com/api/documents/"),
        )
        resp = auditor_client.get("/api/v1/ledger/paperless/documents")

    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "2"
    docs = {d["id"]: d for d in resp.json()}
    assert docs[42]["linked_entry_ids"] == [entry["id"]]
    assert docs[99]["linked_entry_ids"] == []


def test_paperless_documents_restricted_to_configured_document_types(auditor_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PAPERLESS_URL", "https://paperless.example.com")
    monkeypatch.setattr(settings, "PAPERLESS_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "PAPERLESS_DOCUMENT_TYPE_IDS", "3, 7")

    with patch("app.services.paperless.httpx.get") as mock_get:
        mock_get.return_value = httpx.Response(
            200, json={"count": 0, "results": []},
            request=httpx.Request("GET", "https://paperless.example.com/api/documents/"),
        )
        auditor_client.get("/api/v1/ledger/paperless/documents")

    assert mock_get.call_args.kwargs["params"]["document_type__id__in"] == "3,7"


def test_paperless_documents_unreachable_returns_empty(auditor_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "PAPERLESS_URL", "https://paperless.example.com")
    monkeypatch.setattr(settings, "PAPERLESS_API_TOKEN", "test-token")

    with patch("app.services.paperless.httpx.get", side_effect=httpx.ConnectError("unreachable")):
        resp = auditor_client.get("/api/v1/ledger/paperless/documents")

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"
