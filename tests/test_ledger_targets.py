from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.booking_target import BookingTarget
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


def _stage_import_line(db, account, amount, booking_date=date(2026, 5, 2), status=LedgerImportStatus.new):
    """Simulate the destination account's own bank statement having already
    been imported and containing the Gutschrift for this same payout."""
    batch = LedgerImportBatch(bank_account_id=account.id, source=LedgerImportSource.file, imported_by="test")
    db.add(batch)
    db.flush()
    line = LedgerImportLine(
        batch_id=batch.id, bank_account_id=account.id, booking_date=booking_date,
        amount=amount, purpose_text="Bareinzahlung", dedup_hash=f"stage-{account.id}-{amount}-{booking_date}",
        status=status,
    )
    db.add(line)
    db.commit()
    return line


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

def test_set_cash_clearing_account(treasurer_client, clearing_account):
    resp = treasurer_client.put(
        f"/api/v1/ledger/accounts/{clearing_account.id}",
        json={"is_cash_clearing_account": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_cash_clearing_account"] is True


def test_set_cash_clearing_account_rejects_non_offline_account(treasurer_client, bank_account):
    resp = treasurer_client.put(
        f"/api/v1/ledger/accounts/{bank_account.id}",
        json={"is_cash_clearing_account": True},
    )
    assert resp.status_code == 400


def test_setting_cash_clearing_account_clears_others(treasurer_client, db, clearing_account):
    other = BankAccount(name="Anderes Kassenkonto", is_offline=True, opening_balance=Decimal("0.00"))
    db.add(other)
    db.commit()

    treasurer_client.put(f"/api/v1/ledger/accounts/{clearing_account.id}", json={"is_cash_clearing_account": True})
    resp = treasurer_client.put(f"/api/v1/ledger/accounts/{other.id}", json={"is_cash_clearing_account": True})
    assert resp.status_code == 200

    accounts = {a["id"]: a["is_cash_clearing_account"] for a in treasurer_client.get("/api/v1/ledger/accounts").json()}
    assert accounts[clearing_account.id] is False
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
    assert payout["booked_amount"] == "0.00"
    assert payout["remaining_amount"] == "20.00"
    assert payout["entry_ids"] == []
    assert "default_category_id" not in payout


def test_list_target_payouts_filter_booked_false_excludes_booked(
    treasurer_client, db, donation_target, bank_account, clearing_account,
):
    txn = _make_payout(db, donation_target)
    treasurer_client.post(
        "/api/v1/ledger/target-payouts/book",
        json={"payout_transaction_ids": [txn.id], "bank_account_id": bank_account.id, "amount": "20.00"},
    )
    resp = treasurer_client.get("/api/v1/ledger/target-payouts", params={"booked": "false"})
    assert resp.json() == []

    resp = treasurer_client.get("/api/v1/ledger/target-payouts", params={"booked": "true"})
    assert len(resp.json()) == 1
    assert resp.json()[0]["booked"] is True
    assert resp.json()[0]["entry_ids"] != []


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
# POST /ledger/target-payouts/book
# ---------------------------------------------------------------------------

def _book(client, payout_ids, bank_account_id, amount, **extra):
    body = {"payout_transaction_ids": payout_ids, "bank_account_id": bank_account_id, "amount": amount}
    body.update(extra)
    return client.post("/api/v1/ledger/target-payouts/book", json=body)


def test_auditor_cannot_book_payout(auditor_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target)
    resp = _book(auditor_client, [txn.id], bank_account.id, "20.00")
    assert resp.status_code in (401, 403)


def test_book_payout_is_a_pure_transfer(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="20.00")
    resp = _book(treasurer_client, [txn.id], bank_account.id, "20.00")
    assert resp.status_code == 201
    entry = resp.json()
    assert all(l["category_id"] is None for l in entry["lines"])
    destination_line = next(l for l in entry["lines"] if l["bank_account_id"] == bank_account.id)
    clearing_line = next(l for l in entry["lines"] if l["bank_account_id"] == clearing_account.id)
    assert destination_line["amount"] == "20.00"
    assert clearing_line["amount"] == "-20.00"

    [payout] = treasurer_client.get("/api/v1/ledger/target-payouts").json()
    assert payout["booked"] is True
    assert payout["booked_amount"] == "20.00"
    assert payout["remaining_amount"] == "0.00"
    assert payout["entry_ids"] == [entry["id"]]


def test_book_payout_without_clearing_account_400(treasurer_client, db, donation_target, bank_account):
    txn = _make_payout(db, donation_target)
    resp = _book(treasurer_client, [txn.id], bank_account.id, "20.00")
    assert resp.status_code == 400


def test_book_payout_destination_equals_clearing_account_400(treasurer_client, db, donation_target, clearing_account):
    txn = _make_payout(db, donation_target)
    resp = _book(treasurer_client, [txn.id], clearing_account.id, "20.00")
    assert resp.status_code == 400


def test_book_payout_twice_conflicts(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target)
    _book(treasurer_client, [txn.id], bank_account.id, "20.00")
    resp = _book(treasurer_client, [txn.id], bank_account.id, "20.00")
    assert resp.status_code == 409


def test_book_unknown_transaction_404(treasurer_client, bank_account, clearing_account):
    resp = _book(treasurer_client, [9999], bank_account.id, "20.00")
    assert resp.status_code == 404


def test_book_non_payout_transaction_404(treasurer_client, db, test_user, bank_account, clearing_account):
    txn = Transaction(user_id=test_user.id, amount=Decimal("5.00"), type=TransactionType.topup)
    db.add(txn)
    db.commit()
    db.refresh(txn)
    resp = _book(treasurer_client, [txn.id], bank_account.id, "5.00")
    assert resp.status_code == 404


def test_book_payout_unknown_bank_account_404(treasurer_client, db, donation_target, clearing_account):
    txn = _make_payout(db, donation_target)
    resp = _book(treasurer_client, [txn.id], 9999, "20.00")
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

    resp1 = _book(treasurer_client, [txn1.id], private.id, "45.00")
    resp2 = _book(treasurer_client, [txn2.id], vereinskonto.id, "12.50")
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["id"] != resp2.json()["id"]


# ---------------------------------------------------------------------------
# Splitting one payout across several transfers (e.g. a bank transfer-amount
# limit) and bundling several payouts into one transfer
# ---------------------------------------------------------------------------

def test_book_payout_partial_amount_leaves_it_open(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="500.00")
    resp = _book(treasurer_client, [txn.id], bank_account.id, "300.00")
    assert resp.status_code == 201

    [payout] = treasurer_client.get("/api/v1/ledger/target-payouts").json()
    assert payout["booked"] is False
    assert payout["booked_amount"] == "300.00"
    assert payout["remaining_amount"] == "200.00"


def test_book_payout_split_across_two_transfers_completes_it(
    treasurer_client, db, donation_target, clearing_account,
):
    acc_a = BankAccount(iban="DE89370400440532013000", name="Konto A", opening_balance=Decimal("0.00"))
    acc_b = BankAccount(iban="DE02120300000000202051", name="Konto B", opening_balance=Decimal("0.00"))
    db.add_all([acc_a, acc_b])
    db.commit()
    txn = _make_payout(db, donation_target, amount="500.00")

    first = _book(treasurer_client, [txn.id], acc_a.id, "300.00")
    second = _book(treasurer_client, [txn.id], acc_b.id, "200.00")
    assert first.status_code == 201
    assert second.status_code == 201

    [payout] = treasurer_client.get("/api/v1/ledger/target-payouts").json()
    assert payout["booked"] is True
    assert payout["booked_amount"] == "500.00"
    assert payout["remaining_amount"] == "0.00"
    assert sorted(payout["entry_ids"]) == sorted([first.json()["id"], second.json()["id"]])


def test_book_payout_amount_exceeding_remaining_rejected(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="500.00")
    _book(treasurer_client, [txn.id], bank_account.id, "300.00")
    resp = _book(treasurer_client, [txn.id], bank_account.id, "300.00")  # only 200.00 remains
    assert resp.status_code == 400


def test_bundle_two_payouts_into_one_transfer(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn1 = _make_payout(db, donation_target, amount="20.00")
    txn2 = _make_payout(db, donation_target, amount="30.00")

    resp = _book(treasurer_client, [txn1.id, txn2.id], bank_account.id, "50.00")
    assert resp.status_code == 201
    entry = resp.json()
    destination_line = next(l for l in entry["lines"] if l["bank_account_id"] == bank_account.id)
    assert destination_line["amount"] == "50.00"

    payouts = {p["transaction_id"]: p for p in treasurer_client.get("/api/v1/ledger/target-payouts").json()}
    assert payouts[txn1.id]["booked"] is True
    assert payouts[txn1.id]["booked_amount"] == "20.00"
    assert payouts[txn2.id]["booked"] is True
    assert payouts[txn2.id]["booked_amount"] == "30.00"
    assert payouts[txn1.id]["entry_ids"] == [entry["id"]]
    assert payouts[txn2.id]["entry_ids"] == [entry["id"]]


def test_bundle_amount_must_equal_exact_sum(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn1 = _make_payout(db, donation_target, amount="20.00")
    txn2 = _make_payout(db, donation_target, amount="30.00")
    resp = _book(treasurer_client, [txn1.id, txn2.id], bank_account.id, "49.99")
    assert resp.status_code == 400


def test_bundle_rejects_an_already_partially_booked_payout(
    treasurer_client, db, donation_target, bank_account, clearing_account,
):
    txn1 = _make_payout(db, donation_target, amount="20.00")
    txn2 = _make_payout(db, donation_target, amount="30.00")
    _book(treasurer_client, [txn1.id], bank_account.id, "10.00")  # partially book txn1 first

    resp = _book(treasurer_client, [txn1.id, txn2.id], bank_account.id, "40.00")  # remaining(10) + full(30)
    assert resp.status_code == 400


def test_bundle_rejects_an_already_fully_booked_payout(
    treasurer_client, db, donation_target, bank_account, clearing_account,
):
    txn1 = _make_payout(db, donation_target, amount="20.00")
    txn2 = _make_payout(db, donation_target, amount="30.00")
    _book(treasurer_client, [txn1.id], bank_account.id, "20.00")

    resp = _book(treasurer_client, [txn1.id, txn2.id], bank_account.id, "30.00")
    assert resp.status_code == 409


def test_reversing_one_leg_of_a_split_only_reopens_that_slice(
    treasurer_client, db, donation_target, clearing_account,
):
    acc_a = BankAccount(iban="DE89370400440532013000", name="Konto A", opening_balance=Decimal("0.00"))
    acc_b = BankAccount(iban="DE02120300000000202051", name="Konto B", opening_balance=Decimal("0.00"))
    db.add_all([acc_a, acc_b])
    db.commit()
    txn = _make_payout(db, donation_target, amount="500.00")
    first = _book(treasurer_client, [txn.id], acc_a.id, "300.00").json()
    second = _book(treasurer_client, [txn.id], acc_b.id, "200.00").json()

    treasurer_client.post(f"/api/v1/ledger/entries/{first['id']}/reverse")

    [payout] = treasurer_client.get("/api/v1/ledger/target-payouts").json()
    assert payout["booked"] is False
    assert payout["booked_amount"] == "200.00"  # only the second leg remains booked
    assert payout["remaining_amount"] == "300.00"
    assert payout["entry_ids"] == [second["id"]]


def test_reversing_a_bundle_reopens_both_payouts_fully(
    treasurer_client, db, donation_target, bank_account, clearing_account,
):
    txn1 = _make_payout(db, donation_target, amount="20.00")
    txn2 = _make_payout(db, donation_target, amount="30.00")
    entry = _book(treasurer_client, [txn1.id, txn2.id], bank_account.id, "50.00").json()

    treasurer_client.post(f"/api/v1/ledger/entries/{entry['id']}/reverse")

    payouts = {p["transaction_id"]: p for p in treasurer_client.get("/api/v1/ledger/target-payouts").json()}
    assert payouts[txn1.id]["booked"] is False
    assert payouts[txn1.id]["remaining_amount"] == "20.00"
    assert payouts[txn2.id]["booked"] is False
    assert payouts[txn2.id]["remaining_amount"] == "30.00"


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
    _book(treasurer_client, [txn.id], bank_account.id, "30.00")

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


# ---------------------------------------------------------------------------
# Booking a payout matched against an already-imported staged line
# ---------------------------------------------------------------------------

def test_book_payout_matched_to_staged_line(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="30.00")
    staged = _stage_import_line(db, bank_account, Decimal("30.00"))

    resp = _book(treasurer_client, [txn.id], bank_account.id, "30.00", matched_import_line_id=staged.id)
    assert resp.status_code == 201

    updated = treasurer_client.get(f"/api/v1/ledger/import/lines?bank_account_id={bank_account.id}&status=new").json()
    assert updated == []
    booked = treasurer_client.get(
        f"/api/v1/ledger/import/lines?bank_account_id={bank_account.id}&status=booked"
    ).json()
    assert booked[0]["id"] == staged.id
    assert booked[0]["matched_entry_id"] == resp.json()["id"]


def test_book_payout_matches_duplicate_status_staged_line(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="30.00")
    staged = _stage_import_line(db, bank_account, Decimal("30.00"), status=LedgerImportStatus.duplicate)

    resp = _book(treasurer_client, [txn.id], bank_account.id, "30.00", matched_import_line_id=staged.id)
    assert resp.status_code == 201


def test_book_payout_matched_line_wrong_account_rejected(treasurer_client, db, donation_target, bank_account, clearing_account):
    other = BankAccount(iban="DE00999999990000000000", name="Sparkonto")
    db.add(other)
    db.commit()
    txn = _make_payout(db, donation_target, amount="30.00")
    staged = _stage_import_line(db, other, Decimal("30.00"))

    resp = _book(treasurer_client, [txn.id], bank_account.id, "30.00", matched_import_line_id=staged.id)
    assert resp.status_code == 400


def test_book_payout_matched_line_wrong_amount_rejected(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="30.00")
    staged = _stage_import_line(db, bank_account, Decimal("25.00"))

    resp = _book(treasurer_client, [txn.id], bank_account.id, "30.00", matched_import_line_id=staged.id)
    assert resp.status_code == 400


def test_book_payout_matched_line_already_booked_rejected(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="30.00")
    staged = _stage_import_line(db, bank_account, Decimal("30.00"), status=LedgerImportStatus.booked)

    resp = _book(treasurer_client, [txn.id], bank_account.id, "30.00", matched_import_line_id=staged.id)
    assert resp.status_code == 400


def test_book_payout_matched_line_not_found_404(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="30.00")

    resp = _book(treasurer_client, [txn.id], bank_account.id, "30.00", matched_import_line_id=9999)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Free-text search on import lines (?q=) — the fallback for when a
# split/bundled booking makes exact-amount candidate matching useless
# ---------------------------------------------------------------------------

def test_search_q_matches_purpose_text(treasurer_client, db, bank_account):
    _stage_import_line(db, bank_account, Decimal("10.00"))  # purpose_text="Bareinzahlung" by default
    resp = treasurer_client.get("/api/v1/ledger/import/lines", params={"q": "einzahl"})
    assert len(resp.json()) == 1


def test_search_q_matches_counterparty_name(treasurer_client, db, bank_account):
    batch = LedgerImportBatch(bank_account_id=bank_account.id, source=LedgerImportSource.file, imported_by="test")
    db.add(batch)
    db.flush()
    line = LedgerImportLine(
        batch_id=batch.id, bank_account_id=bank_account.id, booking_date=date(2026, 5, 2),
        amount=Decimal("10.00"), counterparty_name="Erika Musterfrau", dedup_hash="search-test",
        status=LedgerImportStatus.new,
    )
    db.add(line)
    db.commit()

    resp = treasurer_client.get("/api/v1/ledger/import/lines", params={"q": "musterfrau"})
    assert len(resp.json()) == 1
    resp = treasurer_client.get("/api/v1/ledger/import/lines", params={"q": "nonexistent"})
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Reversing a booked payout frees it up to be re-booked
# ---------------------------------------------------------------------------

def test_reversing_payout_entry_reopens_it_for_rebooking(treasurer_client, db, donation_target, bank_account, clearing_account):
    txn = _make_payout(db, donation_target, amount="30.00")
    booked = _book(treasurer_client, [txn.id], bank_account.id, "30.00").json()

    # still shows as booked, and can't be booked again while the original stands
    payouts = treasurer_client.get("/api/v1/ledger/target-payouts", params={"booked": "true"}).json()
    assert payouts[0]["entry_ids"] == [booked["id"]]
    assert _book(treasurer_client, [txn.id], bank_account.id, "30.00").status_code == 409

    reverse_resp = treasurer_client.post(f"/api/v1/ledger/entries/{booked['id']}/reverse")
    assert reverse_resp.status_code == 201

    # original entry's link is gone, payout shows as open again
    payouts = treasurer_client.get("/api/v1/ledger/target-payouts", params={"booked": "false"}).json()
    assert len(payouts) == 1
    assert payouts[0]["transaction_id"] == txn.id
    assert payouts[0]["entry_ids"] == []

    # and can now be booked cleanly again
    rebooked = _book(treasurer_client, [txn.id], bank_account.id, "30.00", description="Korrekt gebucht")
    assert rebooked.status_code == 201
    assert rebooked.json()["id"] != booked["id"]
