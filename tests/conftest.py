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


@pytest.fixture
def admin_client(db):
    """TestClient with require_admin_user, require_product_manager_user and require_device_or_admin overridden."""
    from app.auth.deps import require_admin_user, require_device_or_admin, require_product_manager_user

    fake_admin = {"sub": "test-admin-sub", "name": "Test Admin"}

    def override_get_db():
        yield db

    def override_admin():
        return fake_admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_admin_user] = override_admin
    app.dependency_overrides[require_product_manager_user] = override_admin
    app.dependency_overrides[require_device_or_admin] = override_admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def product_manager_client(db):
    """TestClient with only require_product_manager_user overridden (not admin)."""
    from app.auth.deps import require_product_manager_user

    fake_pm = {"sub": "test-pm-sub", "name": "Test Product Manager"}

    def override_get_db():
        yield db

    def override_pm():
        return fake_pm

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_product_manager_user] = override_pm
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
