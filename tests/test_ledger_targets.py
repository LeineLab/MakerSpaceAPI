from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.booking_target import BookingTarget
from app.models.ledger import BankAccount, LedgerCategory, LedgerCategoryKind, LedgerSphere
from app.models.transaction import Transaction, TransactionType

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
def clearing_account(db):
    a = BankAccount(name="Kassenbestand", is_offline=True, is_cash_clearing_account=True, opening_balance=Decimal("0.00"))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def donation_category(db):
    c = LedgerCategory(
        name="Spenden", slug="spenden",
        kind=LedgerCategoryKind.income, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def donation_target(db):
    t = BookingTarget(
        name="Kasse Spenden", slug="kasse-spenden", balance=Decimal("0.00"),
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(t)
    db.commit()
    return t


def _make_payout(db, target, amount="20.00", note=None, when=None):
    """A booking_target_payout transaction — stored negative, same as
    POST /bankomat/payout writes it."""
    txn = Transaction(
        user_id=None,
        amount=-Decimal(amount),
        type=TransactionType.booking_target_payout,
        target_id=target.id,
        note=note,
        created_at=when or datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


def _make_cash_in(db, target, type_, amount, when=None):
    """A topup/booking_target_topup/booking_target_adjustment transaction —
    stored with the same sign convention app/api/v1/bankomat.py itself uses
    (positive for topup/target-topup, signed difference for adjustment)."""
    txn = Transaction(
        user_id=None,
        amount=Decimal(amount),
        type=type_,
        target_id=target.id,
        created_at=when or datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


# ---------------------------------------------------------------------------
# GET/PUT /ledger/targets
# ---------------------------------------------------------------------------

def test_list_targets_requires_auth(client):
    resp = client.get("/api/v1/ledger/targets")
    assert resp.status_code == 401


def test_auditor_can_list_targets(auditor_client, donation_target):
    resp = auditor_client.get("/api/v1/ledger/targets")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["slug"] == "kasse-spenden"
    assert resp.json()[0]["default_category_id"] is None


def test_auditor_cannot_set_mapping(auditor_client, donation_target, donation_category):
    resp = auditor_client.put(
        f"/api/v1/ledger/targets/{donation_target.id}",
        json={"default_category_id": donation_category.id},
    )
    assert resp.status_code in (401, 403)


def test_treasurer_can_set_mapping(treasurer_client, donation_target, donation_category):
    resp = treasurer_client.put(
        f"/api/v1/ledger/targets/{donation_target.id}",
        json={"default_category_id": donation_category.id},
    )
    assert resp.status_code == 200
    assert resp.json()["default_category_id"] == donation_category.id


def test_treasurer_can_clear_mapping(treasurer_client, donation_target, donation_category):
    treasurer_client.put(
        f"/api/v1/ledger/targets/{donation_target.id}",
        json={"default_category_id": donation_category.id},
    )
    resp = treasurer_client.put(
        f"/api/v1/ledger/targets/{donation_target.id}",
        json={"default_category_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["default_category_id"] is None


def test_set_mapping_unknown_category_404(treasurer_client, donation_target):
    resp = treasurer_client.put(
        f"/api/v1/ledger/targets/{donation_target.id}",
        json={"default_category_id": 9999},
    )
    assert resp.status_code == 404


def test_set_mapping_unknown_target_404(treasurer_client, donation_category):
    resp = treasurer_client.put(
        "/api/v1/ledger/targets/9999",
        json={"default_category_id": donation_category.id},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /ledger/accounts/{id} — is_cash_clearing_account exclusivity
# ---------------------------------------------------------------------------

def test_set_cash_clearing_account(treasurer_client, bank_account):
    resp = treasurer_client.put(
        f"/api/v1/ledger/accounts/{bank_account.id}",
        json={"is_cash_clearing_account": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_cash_clearing_account"] is True


def test_setting_cash_clearing_account_clears_others(treasurer_client, db, bank_account):
    other = BankAccount(name="Privatkonto", is_offline=True, opening_balance=Decimal("0.00"))
    db.add(other)
    db.commit()

    treasurer_client.put(f"/api/v1/ledger/accounts/{bank_account.id}", json={"is_cash_clearing_account": True})
    resp = treasurer_client.put(f"/api/v1/ledger/accounts/{other.id}", json={"is_cash_clearing_account": True})
    assert resp.status_code == 200

    accounts = {a["id"]: a["is_cash_clearing_account"] for a in treasurer_client.get("/api/v1/ledger/accounts").json()}
    assert accounts[bank_account.id] is False
    assert accounts[other.id] is True


# ---------------------------------------------------------------------------
# GET /ledger/target-payouts
# ---------------------------------------------------------------------------

def test_list_target_payouts_requires_auth(client):
    resp = client.get("/api/v1/ledger/target-payouts")
    assert resp.status_code == 401


def test_list_target_payouts_unbooked(auditor_client, db, donation_target):
    txn = _make_payout(db, donation_target, amount="20.00", note="Spendenkasse leeren")
    resp = auditor_client.get("/api/v1/ledger/target-payouts")
    assert resp.status_code == 200
    [payout] = resp.json()
    assert payout["transaction_id"] == txn.id
    assert payout["target_slug"] == "kasse-spenden"
    assert payout["amount"] == "20.00"
    assert payout["note"] == "Spendenkasse leeren"
    assert payout["booked"] is False
    assert payout["ledger_entry_id"] is None
    assert "default_category_id" not in payout


def test_list_target_payouts_filter_booked_false_excludes_booked(
    treasurer_client, db, donation_target, bank_account, clearing_account,
):
    txn = _make_payout(db, donation_target)
    treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": bank_account.id},
    )
    resp = treasurer_client.get("/api/v1/ledger/target-payouts", params={"booked": "false"})
    assert resp.json() == []

    resp = treasurer_client.get("/api/v1/ledger/target-payouts", params={"booked": "true"})
    assert len(resp.json()) == 1
    assert resp.json()[0]["booked"] is True
    assert resp.json()[0]["ledger_entry_id"] is not None


def test_list_target_payouts_filter_by_target_id(auditor_client, db, donation_target):
    other = BookingTarget(name="Kasse NFC", slug="kasse-nfc", balance=Decimal("0.00"))
    db.add(other)
    db.commit()
    _make_payout(db, donation_target)
    _make_payout(db, other)

    resp = auditor_client.get("/api/v1/ledger/target-payouts", params={"target_id": donation_target.id})
    assert len(resp.json()) == 1
    assert resp.json()[0]["target_slug"] == "kasse-spenden"


# ---------------------------------------------------------------------------
# POST /ledger/target-payouts/{id}/book
# ---------------------------------------------------------------------------

def test_auditor_cannot_book_payout(auditor_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target)
    resp = auditor_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": bank_account.id},
    )
    assert resp.status_code in (401, 403)


def test_book_payout_is_a_pure_transfer(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="20.00")
    resp = treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": bank_account.id},
    )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["booking_target_payout_id"] == txn.id
    assert all(l["category_id"] is None for l in entry["lines"])
    destination_line = next(l for l in entry["lines"] if l["bank_account_id"] == bank_account.id)
    clearing_line = next(l for l in entry["lines"] if l["bank_account_id"] == clearing_account.id)
    assert destination_line["amount"] == "20.00"
    assert clearing_line["amount"] == "-20.00"


def test_book_payout_without_clearing_account_400(treasurer_client, db, donation_target, bank_account):
    txn = _make_payout(db, donation_target)
    resp = treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": bank_account.id},
    )
    assert resp.status_code == 400


def test_book_payout_destination_equals_clearing_account_400(treasurer_client, db, donation_target, clearing_account):
    txn = _make_payout(db, donation_target)
    resp = treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": clearing_account.id},
    )
    assert resp.status_code == 400


def test_book_payout_twice_conflicts(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target)
    treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": bank_account.id},
    )
    resp = treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": bank_account.id},
    )
    assert resp.status_code == 409


def test_book_unknown_transaction_404(treasurer_client, bank_account, clearing_account):
    resp = treasurer_client.post(
        "/api/v1/ledger/target-payouts/9999/book",
        json={"bank_account_id": bank_account.id},
    )
    assert resp.status_code == 404


def test_book_non_payout_transaction_404(treasurer_client, db, test_user, bank_account, clearing_account):
    txn = Transaction(user_id=test_user.id, amount=Decimal("5.00"), type=TransactionType.topup)
    db.add(txn)
    db.commit()
    db.refresh(txn)
    resp = treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": bank_account.id},
    )
    assert resp.status_code == 404


def test_book_payout_unknown_bank_account_404(treasurer_client, db, donation_target, clearing_account):
    txn = _make_payout(db, donation_target)
    resp = treasurer_client.post(
        f"/api/v1/ledger/target-payouts/{txn.id}/book",
        json={"bank_account_id": 9999},
    )
    assert resp.status_code == 404


def test_book_payout_two_targets_to_different_destinations(
    treasurer_client, db, donation_target, clearing_account,
):
    """Mirrors the real workflow: each target's payout can be booked to a
    different destination account (e.g. a private account first, later
    transferred on to the real Vereinskonto via a separate manual entry —
    that second hop isn't part of this bridge at all)."""
    other_target = BookingTarget(name="Kasse NFC", slug="kasse-nfc", balance=Decimal("0.00"))
    db.add(other_target)
    private = BankAccount(iban="DE89370400440532013000", name="Privatkonto", opening_balance=Decimal("0.00"))
    vereinskonto = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("0.00"))
    db.add(private)
    db.add(vereinskonto)
    db.commit()

    txn1 = _make_payout(db, donation_target, amount="45.00")
    txn2 = _make_payout(db, other_target, amount="12.50")

    resp1 = treasurer_client.post(f"/api/v1/ledger/target-payouts/{txn1.id}/book", json={"bank_account_id": private.id})
    resp2 = treasurer_client.post(f"/api/v1/ledger/target-payouts/{txn2.id}/book", json={"bank_account_id": vereinskonto.id})
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["id"] != resp2.json()["id"]


# ---------------------------------------------------------------------------
# EÜR aggregation folds in Kassen cash-in events (topup/target-topup/adjustment)
# ---------------------------------------------------------------------------

def test_euer_report_includes_target_topup(treasurer_client, db, donation_target, donation_category):
    treasurer_client.put(f"/api/v1/ledger/targets/{donation_target.id}", json={"default_category_id": donation_category.id})
    _make_cash_in(db, donation_target, TransactionType.booking_target_topup, "30.00", when=datetime(2026, 5, 1))

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2026})
    assert resp.status_code == 200
    [cat] = resp.json()["categories"]
    assert cat["category_id"] == donation_category.id
    assert cat["total"] == "30.00"
    assert resp.json()["total_income"] == "30.00"


def test_euer_report_includes_topup(treasurer_client, db, donation_target, donation_category, test_user):
    treasurer_client.put(f"/api/v1/ledger/targets/{donation_target.id}", json={"default_category_id": donation_category.id})
    db.add(Transaction(
        user_id=test_user.id, amount=Decimal("10.00"), type=TransactionType.topup,
        target_id=donation_target.id, created_at=datetime(2026, 3, 1),
    ))
    db.commit()

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2026})
    assert resp.json()["total_income"] == "10.00"


def test_euer_report_shortfall_reduces_income(treasurer_client, db, donation_target, donation_category):
    treasurer_client.put(f"/api/v1/ledger/targets/{donation_target.id}", json={"default_category_id": donation_category.id})
    _make_cash_in(db, donation_target, TransactionType.booking_target_topup, "30.00", when=datetime(2026, 5, 1))
    # a Fehlbestand write-off: actual_balance < balance -> negative difference
    _make_cash_in(db, donation_target, TransactionType.booking_target_adjustment, "-5.00", when=datetime(2026, 6, 1))

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2026})
    assert resp.json()["total_income"] == "25.00"


def test_euer_report_ignores_target_without_mapping(treasurer_client, db, donation_target):
    # no default_category_id set on donation_target
    _make_cash_in(db, donation_target, TransactionType.booking_target_topup, "30.00", when=datetime(2026, 5, 1))

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2026})
    assert resp.json()["categories"] == []
    assert resp.json()["total_income"] == "0.00"


def test_euer_report_ignores_other_years(treasurer_client, db, donation_target, donation_category):
    treasurer_client.put(f"/api/v1/ledger/targets/{donation_target.id}", json={"default_category_id": donation_category.id})
    _make_cash_in(db, donation_target, TransactionType.booking_target_topup, "30.00", when=datetime(2025, 12, 31, 23, 59))

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2026})
    assert resp.json()["total_income"] == "0.00"

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2025})
    assert resp.json()["total_income"] == "30.00"


def test_euer_report_ignores_booked_payout_transfer(
    treasurer_client, db, donation_target, donation_category, bank_account, clearing_account,
):
    """A booked payout is a pure bank-to-bank transfer (no category line) —
    it must not additionally show up in the EÜR (it isn't new income, the
    cash was already recognized via the topup/target-topup that put it into
    the target in the first place)."""
    treasurer_client.put(f"/api/v1/ledger/targets/{donation_target.id}", json={"default_category_id": donation_category.id})
    _make_cash_in(db, donation_target, TransactionType.booking_target_topup, "30.00", when=datetime(2026, 5, 1))
    txn = _make_payout(db, donation_target, amount="30.00", when=datetime(2026, 5, 2))
    treasurer_client.post(f"/api/v1/ledger/target-payouts/{txn.id}/book", json={"bank_account_id": bank_account.id})

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2026})
    assert resp.json()["total_income"] == "30.00"


def test_euer_report_combines_manual_and_target_income(
    treasurer_client, db, donation_target, donation_category,
):
    """A category already used by a normal manual booking still aggregates
    correctly once Kassen cash-in events for the same category are added."""
    treasurer_client.put(f"/api/v1/ledger/targets/{donation_target.id}", json={"default_category_id": donation_category.id})
    bank = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("0.00"))
    db.add(bank)
    db.commit()
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-04-01",
            "description": "Direktspende per Überweisung",
            "lines": [
                {"bank_account_id": bank.id, "amount": "100.00"},
                {"category_id": donation_category.id, "amount": "-100.00"},
            ],
        },
    )
    _make_cash_in(db, donation_target, TransactionType.booking_target_topup, "30.00", when=datetime(2026, 5, 1))

    resp = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2026})
    assert resp.json()["total_income"] == "130.00"
