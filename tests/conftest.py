import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base


@pytest.fixture
def db():
    """Fresh in-memory SQLite database per test — no isolation complexity."""
    engine = create_engine(
        "sqlite://",  # in-memory, discarded after test
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # single shared connection so create_all and queries see same DB
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def machine_token(db):
    """Create a test machine and return its plaintext API token."""
    from datetime import UTC, datetime

    from app.auth.tokens import generate_api_token
    from app.models.machine import Machine

    token, token_hash = generate_api_token()
    machine = Machine(
        name="Test Machine",
        slug="test-machine",
        machine_type="machine",
        api_token_hash=token_hash,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        active=True,
    )
    db.add(machine)
    db.commit()
    return token, machine


@pytest.fixture
def test_user(db):
    """Create a test user."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.models.user import User

    user = User(id=123456789, name="Test User", balance=Decimal("50.00"), created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def checkout_token(db):
    """Create a checkout-type machine and return its plaintext API token."""
    from datetime import UTC, datetime

    from app.auth.tokens import generate_api_token
    from app.models.machine import Machine

    token, token_hash = generate_api_token()
    machine = Machine(
        name="Checkout Box",
        slug="checkout-box",
        machine_type="checkout",
        api_token_hash=token_hash,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        active=True,
    )
    db.add(machine)
    db.commit()
    return token, machine


def _role_client(db, sub, groups):
    """Build a TestClient authenticated as a real (signed) admin JWT cookie
    carrying the given OIDC groups — not a dependency_overrides fake identity.

    Only `get_db` is overridden (intentionally shared across every client
    fixture used in a test, so they all see the same in-memory DB). Auth goes
    through the real verify_admin_jwt/is_admin/is_* checks. This matters
    because dependency_overrides lives on the shared `app` object: if two
    `_client`-style fixtures were both auth-overridden and combined as
    parameters in one test, whichever fixture set up last would silently win
    for *every* request in that test regardless of which client variable
    made the call — invisible until a test asserts a restricted (401/403)
    outcome. A real per-cookie identity is genuinely isolated per TestClient
    (each has its own cookie jar), so multiple role fixtures can safely
    coexist in the same test now.
    """
    from app.auth.jwt import create_admin_jwt

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    token = create_admin_jwt({"sub": sub, "groups": groups})
    with TestClient(app) as c:
        c.cookies.set("auth_token", token)
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(db):
    """TestClient authenticated as an admin (real JWT, OIDC_ADMIN_GROUP membership)."""
    from app.config import settings

    yield from _role_client(db, "test-admin-sub", [settings.OIDC_ADMIN_GROUP])


@pytest.fixture
def product_manager_client(db, monkeypatch):
    """TestClient authenticated as a product manager who is NOT an admin."""
    from app.config import settings

    monkeypatch.setattr(settings, "OIDC_PRODUCT_MANAGER_GROUP", "test-product-managers")
    yield from _role_client(db, "test-pm-sub", ["test-product-managers"])


@pytest.fixture
def treasurer_client(db, monkeypatch):
    """TestClient authenticated as a treasurer (Kassenwart) who is NOT an admin."""
    from app.config import settings

    monkeypatch.setattr(settings, "OIDC_TREASURER_GROUP", "test-treasurers")
    yield from _role_client(db, "test-treasurer-sub", ["test-treasurers"])


@pytest.fixture
def auditor_client(db, monkeypatch):
    """TestClient authenticated as a read-only auditor (Kassenprüfer) who is
    NOT a treasurer or admin."""
    from app.config import settings

    monkeypatch.setattr(settings, "OIDC_AUDITOR_GROUP", "test-auditors")
    yield from _role_client(db, "test-auditor-sub", ["test-auditors"])
