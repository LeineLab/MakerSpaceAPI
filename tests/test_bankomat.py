from datetime import UTC, datetime
from decimal import Decimal

import pytest
from passlib.context import CryptContext

from app.models.booking_target import BookingTarget
from app.models.transaction import Transaction, TransactionType
from app.models.user import User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def target(db):
    """A booking target with 100.00 balance."""
    t = BookingTarget(name="Test Fund", slug="test-fund", balance=Decimal("100.00"), created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(t)
    db.commit()
    return t


@pytest.fixture
def second_user(db):
    """A second user for transfer tests."""
    u = User(id=987654321, name="Second User", balance=Decimal("10.00"), created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(u)
    db.commit()
    return u


# ---------------------------------------------------------------------------
# GET /bankomat/targets — public
# ---------------------------------------------------------------------------

def test_list_targets_empty(client):
    resp = client.get("/api/v1/bankomat/targets")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_targets_returns_all(client, target):
    resp = client.get("/api/v1/bankomat/targets")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["slug"] == "test-fund"
    assert Decimal(str(data[0]["balance"])) == Decimal("100.00")


# ---------------------------------------------------------------------------
# POST /bankomat/targets — admin only
# ---------------------------------------------------------------------------

def test_create_target_success(admin_client):
    resp = admin_client.post(
        "/api/v1/bankomat/targets",
        json={"name": "New Fund", "slug": "new-fund"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "new-fund"
    assert Decimal(str(data["balance"])) == Decimal("0.00")


def test_create_target_duplicate_slug(admin_client, target):
    resp = admin_client.post(
        "/api/v1/bankomat/targets",
        json={"name": "Duplicate", "slug": "test-fund"},
    )
    assert resp.status_code == 409


def test_create_target_requires_admin(client):
    resp = client.post(
        "/api/v1/bankomat/targets",
        json={"name": "New Fund", "slug": "new-fund"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /bankomat/topup
# ---------------------------------------------------------------------------

def test_topup_user_not_found(client, machine_token, target):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/topup",
        json={"nfc_id": 999999, "amount": "10.00", "target_slug": "test-fund"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_topup_target_not_found(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/topup",
        json={"nfc_id": test_user.id, "amount": "10.00", "target_slug": "no-such-target"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_topup_non_positive_amount(client, machine_token, test_user, target):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/topup",
        json={"nfc_id": test_user.id, "amount": "0.00", "target_slug": "test-fund"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_topup_success(client, machine_token, test_user, target, db):
    token, machine = machine_token
    user_balance_before = test_user.balance
    target_balance_before = target.balance
    amount = Decimal("20.00")

    resp = client.post(
        "/api/v1/bankomat/topup",
        json={"nfc_id": test_user.id, "amount": str(amount), "target_slug": "test-fund"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db.refresh(test_user)
    db.refresh(target)
    assert test_user.balance == user_balance_before + amount
    assert target.balance == target_balance_before + amount

    tx = (
        db.query(Transaction)
        .filter(Transaction.user_id == test_user.id, Transaction.type == TransactionType.topup)
        .first()
    )
    assert tx is not None
    assert tx.amount == amount
    assert tx.target_id == target.id


def test_topup_requires_device_token(client, test_user, target):
    resp = client.post(
        "/api/v1/bankomat/topup",
        json={"nfc_id": test_user.id, "amount": "10.00", "target_slug": "test-fund"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /bankomat/target-topup
# ---------------------------------------------------------------------------

def test_target_topup_success(client, machine_token, test_user, target, db):
    """target-topup increases target balance but NOT user balance."""
    token, _ = machine_token
    user_balance_before = test_user.balance
    target_balance_before = target.balance
    amount = Decimal("5.00")

    resp = client.post(
        "/api/v1/bankomat/target-topup",
        json={"amount": str(amount), "target_slug": "test-fund"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db.refresh(test_user)
    db.refresh(target)
    assert test_user.balance == user_balance_before  # unchanged
    assert target.balance == target_balance_before + amount


def test_target_topup_target_not_found(client, machine_token):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/target-topup",
        json={"amount": "5.00", "target_slug": "ghost"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_target_topup_non_positive(client, machine_token, target):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/target-topup",
        json={"amount": "-1.00", "target_slug": "test-fund"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /bankomat/transactions/{nfc_id}
# ---------------------------------------------------------------------------

def test_transactions_empty(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/bankomat/transactions/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_transactions_returned_newest_first(client, machine_token, test_user, target, db):
    token, machine = machine_token
    # Insert two topup transactions directly
    t1 = Transaction(
        user_id=test_user.id, amount=Decimal("1.00"),
        type=TransactionType.topup, machine_id=machine.id,
        created_at=datetime(2024, 1, 1),
    )
    t2 = Transaction(
        user_id=test_user.id, amount=Decimal("2.00"),
        type=TransactionType.topup, machine_id=machine.id,
        created_at=datetime(2024, 1, 2),
    )
    db.add_all([t1, t2])
    db.commit()

    resp = client.get(
        f"/api/v1/bankomat/transactions/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert Decimal(str(data[0]["amount"])) == Decimal("2.00")  # newest first
    assert Decimal(str(data[1]["amount"])) == Decimal("1.00")


def test_transactions_limit(client, machine_token, test_user, db):
    token, machine = machine_token
    for i in range(5):
        db.add(Transaction(
            user_id=test_user.id, amount=Decimal("1.00"),
            type=TransactionType.topup, machine_id=machine.id,
        ))
    db.commit()

    resp = client.get(
        f"/api/v1/bankomat/transactions/{test_user.id}?limit=3",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# POST /bankomat/transfer
# ---------------------------------------------------------------------------

def test_transfer_same_user(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/transfer",
        json={"from_nfc_id": test_user.id, "to_nfc_id": test_user.id, "amount": "5.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_transfer_non_positive(client, machine_token, test_user, second_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/transfer",
        json={"from_nfc_id": test_user.id, "to_nfc_id": second_user.id, "amount": "0.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_transfer_sender_not_found(client, machine_token, second_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/transfer",
        json={"from_nfc_id": 999999, "to_nfc_id": second_user.id, "amount": "5.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_transfer_recipient_not_found(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/transfer",
        json={"from_nfc_id": test_user.id, "to_nfc_id": 999999, "amount": "5.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_transfer_insufficient_balance(client, machine_token, test_user, second_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/transfer",
        json={"from_nfc_id": test_user.id, "to_nfc_id": second_user.id, "amount": "999.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402


def test_transfer_success(client, machine_token, test_user, second_user, db):
    token, machine = machine_token
    amount = Decimal("15.00")
    sender_before = test_user.balance
    recipient_before = second_user.balance

    resp = client.post(
        "/api/v1/bankomat/transfer",
        json={
            "from_nfc_id": test_user.id,
            "to_nfc_id": second_user.id,
            "amount": str(amount),
            "note": "test transfer",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db.refresh(test_user)
    db.refresh(second_user)
    assert test_user.balance == sender_before - amount
    assert second_user.balance == recipient_before + amount

    # Paired transactions with same timestamp
    tx_out = (
        db.query(Transaction)
        .filter(Transaction.user_id == test_user.id, Transaction.type == TransactionType.transfer_out)
        .first()
    )
    tx_in = (
        db.query(Transaction)
        .filter(Transaction.user_id == second_user.id, Transaction.type == TransactionType.transfer_in)
        .first()
    )
    assert tx_out is not None and tx_in is not None
    assert tx_out.amount == -amount
    assert tx_in.amount == amount
    assert tx_out.peer_user_id == second_user.id
    assert tx_in.peer_user_id == test_user.id
    assert tx_out.created_at == tx_in.created_at  # same timestamp


# ---------------------------------------------------------------------------
# POST /bankomat/payout
# ---------------------------------------------------------------------------

def test_payout_non_positive(client, machine_token, test_user, target):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/payout",
        json={"nfc_id": test_user.id, "pin": "1234", "target_slug": "test-fund", "amount": "0.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_payout_user_not_found(client, machine_token, target):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/payout",
        json={"nfc_id": 999999, "pin": "1234", "target_slug": "test-fund", "amount": "10.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_payout_no_pin_set(client, machine_token, test_user, target):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/bankomat/payout",
        json={"nfc_id": test_user.id, "pin": "1234", "target_slug": "test-fund", "amount": "10.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_payout_wrong_pin(client, machine_token, test_user, target, db):
    token, _ = machine_token
    test_user.pin_hash = _pwd.hash("correct-pin")
    db.commit()

    resp = client.post(
        "/api/v1/bankomat/payout",
        json={"nfc_id": test_user.id, "pin": "wrong-pin", "target_slug": "test-fund", "amount": "10.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_payout_target_not_found(client, machine_token, test_user, db):
    token, _ = machine_token
    test_user.pin_hash = _pwd.hash("1234")
    db.commit()

    resp = client.post(
        "/api/v1/bankomat/payout",
        json={"nfc_id": test_user.id, "pin": "1234", "target_slug": "ghost", "amount": "10.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_payout_insufficient_target_balance(client, machine_token, test_user, target, db):
    token, _ = machine_token
    test_user.pin_hash = _pwd.hash("1234")
    db.commit()

    resp = client.post(
        "/api/v1/bankomat/payout",
        json={"nfc_id": test_user.id, "pin": "1234", "target_slug": "test-fund", "amount": "999.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402


def test_payout_success(client, machine_token, test_user, target, db):
    token, machine = machine_token
    test_user.pin_hash = _pwd.hash("secret")
    db.commit()
    target_balance_before = target.balance
    amount = Decimal("30.00")

    resp = client.post(
        "/api/v1/bankomat/payout",
        json={
            "nfc_id": test_user.id, "pin": "secret",
            "target_slug": "test-fund", "amount": str(amount),
            "note": "payout test",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db.refresh(target)
    assert target.balance == target_balance_before - amount

    tx = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == test_user.id,
            Transaction.type == TransactionType.booking_target_payout,
        )
        .first()
    )
    assert tx is not None
    assert tx.amount == -amount
    assert tx.target_id == target.id


# ---------------------------------------------------------------------------
# POST /bankomat/pin — admin only
# ---------------------------------------------------------------------------

def test_set_pin_requires_admin(client, test_user):
    resp = client.post(
        "/api/v1/bankomat/pin",
        json={"nfc_id": test_user.id, "pin": "1234"},
    )
    assert resp.status_code == 401


def test_set_pin_user_not_found(admin_client):
    resp = admin_client.post(
        "/api/v1/bankomat/pin",
        json={"nfc_id": 999999, "pin": "1234"},
    )
    assert resp.status_code == 404


def test_set_pin_success(admin_client, test_user, db):
    resp = admin_client.post(
        "/api/v1/bankomat/pin",
        json={"nfc_id": test_user.id, "pin": "my-secret-pin"},
    )
    assert resp.status_code == 200

    db.refresh(test_user)
    assert test_user.pin_hash is not None
    assert _pwd.verify("my-secret-pin", test_user.pin_hash)


def test_set_pin_replaces_existing(admin_client, test_user, db):
    """Setting PIN twice replaces the old hash."""
    test_user.pin_hash = _pwd.hash("old-pin")
    db.commit()

    admin_client.post(
        "/api/v1/bankomat/pin",
        json={"nfc_id": test_user.id, "pin": "new-pin"},
    )

    db.refresh(test_user)
    assert _pwd.verify("new-pin", test_user.pin_hash)
    assert not _pwd.verify("old-pin", test_user.pin_hash)
