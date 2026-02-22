from decimal import Decimal
from datetime import UTC, datetime, timedelta

import pytest

from app.models.machine import MachineAuthorization
from app.models.session import MachineSession
from app.models.transaction import Transaction, TransactionType


# ---------------------------------------------------------------------------
# Shared fixture: auth record + helper to build sessions
# ---------------------------------------------------------------------------

@pytest.fixture
def authorized(machine_token, test_user, db):
    """
    Grants test_user authorization on the test machine.
    Yields (token, machine, auth).
    Pricing: login=0.00, per_minute=0.10 EUR, interval=10 min → cost per interval = 1.00 EUR
    """
    token, machine = machine_token
    auth = MachineAuthorization(
        machine_id=machine.id,
        user_id=test_user.id,
        price_per_login=Decimal("0.00"),
        price_per_minute=Decimal("0.10"),
        booking_interval=10,
        granted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(auth)
    db.commit()
    return token, machine, auth


def make_session(db, machine_id, user_id, paid_until, *, end_time=None):
    """Helper: insert a MachineSession row directly."""
    s = MachineSession(
        machine_id=machine_id,
        user_id=user_id,
        start_time=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),
        paid_until=paid_until,
        end_time=end_time,
    )
    db.add(s)
    db.commit()
    return s


# ---------------------------------------------------------------------------
# POST /sessions — create
# ---------------------------------------------------------------------------

def test_create_session_user_not_found(client, machine_token):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/sessions",
        json={"nfc_id": 999999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_create_session_not_authorized(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/sessions",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_create_session_insufficient_balance(client, authorized, test_user, db):
    """Balance below login_fee + price_per_minute * interval → 402."""
    token, machine, auth = authorized
    test_user.balance = Decimal("0.50")  # interval cost = 1.00
    db.commit()

    resp = client.post(
        "/api/v1/sessions",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402


def test_create_session_success(client, authorized, test_user, db):
    token, machine, auth = authorized
    balance_before = test_user.balance

    resp = client.post(
        "/api/v1/sessions",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "session_id" in data
    # First interval (10 min) should have been deducted
    assert data["remaining_seconds"] == pytest.approx(10 * 60, abs=2)
    # max_seconds = remaining + (new_balance / price_per_minute) * 60
    db.refresh(test_user)
    expected_max = (10 * 60) + float(test_user.balance / auth.price_per_minute) * 60
    assert data["max_seconds"] == pytest.approx(expected_max, rel=1e-3)
    # Balance deducted by interval cost (login=0, usage=0.10*10=1.00)
    assert test_user.balance == balance_before - Decimal("1.00")


# ---------------------------------------------------------------------------
# PUT /sessions/{id} — extend
# ---------------------------------------------------------------------------

def test_extend_not_found(client, machine_token):
    token, _ = machine_token
    resp = client.put(
        "/api/v1/sessions/999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_extend_already_terminated(client, authorized, test_user, db):
    """PUT on a session that already has end_time set → 409."""
    token, machine, _ = authorized
    now = datetime.now(UTC).replace(tzinfo=None)
    session = make_session(
        db, machine.id, test_user.id,
        paid_until=now - timedelta(minutes=5),
        end_time=now - timedelta(minutes=5),
    )

    resp = client.put(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


def test_extend_still_paid_no_charge(client, authorized, test_user, db):
    """paid_until is in the future → return timing info, no balance deducted."""
    token, machine, _ = authorized
    paid_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=8)
    session = make_session(db, machine.id, test_user.id, paid_until=paid_until)
    balance_before = test_user.balance

    resp = client.put(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["terminated"] is False
    assert data["remaining_seconds"] == pytest.approx(8 * 60, abs=2)
    assert data["max_seconds"] > data["remaining_seconds"]

    db.refresh(test_user)
    assert test_user.balance == balance_before  # no charge


def test_extend_expired_sufficient_balance(client, authorized, test_user, db):
    """paid_until expired, balance sufficient → deduct interval, advance paid_until."""
    token, machine, auth = authorized
    now = datetime.now(UTC).replace(tzinfo=None)
    # Expired 2 minutes ago; paid_until was 2 min in the past
    paid_until = now - timedelta(minutes=2)
    session = make_session(db, machine.id, test_user.id, paid_until=paid_until)
    balance_before = test_user.balance
    interval_cost = auth.price_per_minute * auth.booking_interval  # 0.10 * 10 = 1.00

    resp = client.put(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["terminated"] is False
    # New paid_until = old_paid_until + 10 min.  Remaining ≈ 8 min (10 - 2 already elapsed)
    assert data["remaining_seconds"] == pytest.approx(8 * 60, abs=2)

    db.refresh(test_user)
    assert test_user.balance == balance_before - interval_cost

    db.refresh(session)
    assert session.end_time is None  # still active
    assert session.paid_until > now  # advanced forward

    # A machine_usage transaction was recorded
    tx = (
        db.query(Transaction)
        .filter(
            Transaction.session_id == session.id,
            Transaction.type == TransactionType.machine_usage,
        )
        .order_by(Transaction.id.desc())
        .first()
    )
    assert tx is not None
    assert tx.amount == -interval_cost


def test_extend_expired_insufficient_balance(client, authorized, test_user, db):
    """paid_until expired, balance too low → 402, session end_time set, no deduction."""
    token, machine, _ = authorized
    now = datetime.now(UTC).replace(tzinfo=None)
    test_user.balance = Decimal("0.50")  # interval cost = 1.00 → insufficient
    db.commit()

    session = make_session(
        db, machine.id, test_user.id,
        paid_until=now - timedelta(minutes=1),
    )

    resp = client.put(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402

    db.refresh(session)
    assert session.end_time is not None  # terminated

    db.refresh(test_user)
    assert test_user.balance == Decimal("0.50")  # untouched


def test_extend_max_seconds_calculation(client, authorized, test_user, db):
    """max_seconds = remaining_seconds + (balance / price_per_minute) * 60."""
    token, machine, auth = authorized
    # test_user balance = 50.00, price_per_minute = 0.10 → 500 additional minutes
    paid_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
    session = make_session(db, machine.id, test_user.id, paid_until=paid_until)

    resp = client.put(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    remaining_s = data["remaining_seconds"]
    extra_s = float(test_user.balance / auth.price_per_minute) * 60
    assert data["max_seconds"] == pytest.approx(remaining_s + extra_s, rel=1e-3)


def test_extend_free_machine_max_seconds_is_inf(client, machine_token, test_user, db):
    """price_per_minute = 0 → max_seconds reported as infinity."""
    token, machine = machine_token
    now = datetime.now(UTC).replace(tzinfo=None)
    auth = MachineAuthorization(
        machine_id=machine.id,
        user_id=test_user.id,
        price_per_login=Decimal("0.00"),
        price_per_minute=Decimal("0.00"),
        booking_interval=60,
        granted_at=now,
    )
    db.add(auth)
    session = make_session(
        db, machine.id, test_user.id,
        paid_until=now + timedelta(hours=1),
    )
    db.commit()

    resp = client.put(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["max_seconds"] is None  # None = unlimited (free machine)


def test_extend_wrong_device_gets_404(client, machine_token, test_user, db):
    """A device cannot extend a session that belongs to a different machine."""
    from app.auth.tokens import generate_api_token
    from app.models.machine import Machine

    token, machine = machine_token
    other_token, other_hash = generate_api_token()
    other = Machine(
        name="Other", slug="other", machine_type="machine",
        api_token_hash=other_hash, created_at=datetime.now(UTC).replace(tzinfo=None), active=True,
    )
    db.add(other)
    session = make_session(
        db, machine.id, test_user.id,
        paid_until=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
    )
    db.commit()

    resp = client.put(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /sessions/{id} — terminate
# ---------------------------------------------------------------------------

def test_terminate_session(client, authorized, test_user, db):
    token, machine, _ = authorized
    session = make_session(
        db, machine.id, test_user.id,
        paid_until=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
    )

    resp = client.delete(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    db.refresh(session)
    assert session.end_time is not None


def test_terminate_already_ended_is_idempotent(client, authorized, test_user, db):
    """DELETE on an already-terminated session returns 200 without error."""
    token, machine, _ = authorized
    now = datetime.now(UTC).replace(tzinfo=None)
    session = make_session(
        db, machine.id, test_user.id,
        paid_until=now - timedelta(hours=1),
        end_time=now - timedelta(minutes=5),
    )

    resp = client.delete(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
