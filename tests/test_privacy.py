"""
Privacy tests: verify that no endpoint leaks personal data (user names, NFC IDs,
balances, OIDC subs) in its response body when called without authentication.

Each test creates a user with a known sentinel name/ID, then calls an endpoint
unauthenticated and asserts the sentinel values do not appear anywhere in the
response text — regardless of status code.
"""
import pytest
from decimal import Decimal
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.auth.tokens import generate_api_token
from app.database import get_db
from app.main import app
from app.models.machine import Machine, MachineAuthorization
from app.models.rental import Rental, RentalItem, RentalPermission
from app.models.session import MachineSession
from app.models.transaction import Transaction, TransactionType
from app.models.user import User


SENTINEL_NAME   = "SentinelUserXYZ"
SENTINEL_ID     = 987654321
SENTINEL_SUB    = "oidc|sentinel-sub-xyz"
SENTINEL_SLUG   = "sentinel-machine"


@pytest.fixture
def bare_client(db):
    """TestClient with only the DB overridden — no auth overrides."""
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def pii_data(db):
    """Seed a user + machine with authorization, session, and transaction."""
    now = datetime.now(UTC).replace(tzinfo=None)

    user = User(
        id=SENTINEL_ID,
        name=SENTINEL_NAME,
        oidc_sub=SENTINEL_SUB,
        balance=Decimal("42.00"),
        created_at=now,
    )
    db.add(user)

    _, token_hash = generate_api_token()
    machine = Machine(
        name="Sentinel Machine",
        slug=SENTINEL_SLUG,
        machine_type="machine",
        api_token_hash=token_hash,
        created_at=now,
        active=True,
    )
    db.add(machine)
    db.flush()  # populate machine.id

    db.add(MachineAuthorization(
        machine_id=machine.id,
        user_id=SENTINEL_ID,
        price_per_login=Decimal("0.00"),
        price_per_minute=Decimal("0.05"),
        booking_interval=60,
        granted_at=now,
    ))

    session = MachineSession(
        machine_id=machine.id,
        user_id=SENTINEL_ID,
        start_time=now - timedelta(minutes=5),
        end_time=now,
        paid_until=now,
    )
    db.add(session)
    db.flush()

    db.add(Transaction(
        user_id=SENTINEL_ID,
        amount=Decimal("-0.25"),
        type=TransactionType.machine_usage,
        machine_id=machine.id,
        session_id=session.id,
        created_at=now,
    ))

    rental_item = RentalItem(
        name="Sentinel Tool",
        uhf_tid="E200341201SENTINEL",
        active=True,
        created_at=now,
    )
    db.add(rental_item)
    db.flush()

    db.add(RentalPermission(user_id=SENTINEL_ID, granted_at=now))
    db.add(Rental(item_id=rental_item.id, user_id=SENTINEL_ID, rented_at=now))

    db.commit()
    return user, machine


def _contains_pii(text: str) -> bool:
    """Return True if any sentinel PII value appears in text."""
    return (
        SENTINEL_NAME in text
        or str(SENTINEL_ID) in text
        or SENTINEL_SUB in text
    )


_ENDPOINTS = [
    # User management
    ("GET",  "/api/v1/users"),
    ("GET",  f"/api/v1/users/{SENTINEL_ID}"),
    ("GET",  "/api/v1/users/me"),
    ("GET",  "/api/v1/users/me/transactions"),
    ("GET",  "/api/v1/users/me/rentals"),
    ("GET",  "/api/v1/users/me/machines"),
    ("GET",  "/api/v1/users/me/sessions"),
    # Machine data
    ("GET",  "/api/v1/machines"),
    ("GET",  "/api/v1/machines/my"),
    ("GET",  f"/api/v1/machines/{SENTINEL_SLUG}"),
    ("GET",  f"/api/v1/machines/{SENTINEL_SLUG}/authorizations"),
    ("GET",  f"/api/v1/machines/{SENTINEL_SLUG}/admins"),
    ("GET",  f"/api/v1/machines/{SENTINEL_SLUG}/sessions"),
    # Rentals
    ("GET",  "/api/v1/rentals/active"),
    ("GET",  "/api/v1/rentals/permissions"),
    # Bankomat transaction history
    ("GET",  f"/api/v1/bankomat/transactions/{SENTINEL_ID}"),
]


@pytest.mark.parametrize("method,path", _ENDPOINTS)
def test_no_pii_without_auth(bare_client, pii_data, method, path):
    """Response body must not contain any user PII for unauthenticated requests."""
    resp = bare_client.request(method, path)
    assert not _contains_pii(resp.text), (
        f"{method} {path} (HTTP {resp.status_code}) leaked PII in response body:\n{resp.text[:500]}"
    )
