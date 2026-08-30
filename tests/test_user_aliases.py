from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import require_session_user
from app.database import get_db
from app.main import app
from app.models.transaction import Transaction, TransactionType
from app.models.user import User, UserCardAlias, resolve_nfc_id

ALIAS_ID = 987654321
UNLINKED_ID = 555555555


@pytest.fixture
def session_client(db, test_user):
    """TestClient authenticated as test_user's OIDC identity via require_session_user."""
    test_user.oidc_sub = "test-oidc-sub"
    db.commit()

    def override_get_db():
        yield db

    def override_session_user():
        return {"sub": "test-oidc-sub", "name": "Test User"}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_session_user] = override_session_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _alias(db, alias_id: int, user_id: int) -> UserCardAlias:
    a = UserCardAlias(alias_id=alias_id, user_id=user_id, created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(a)
    db.commit()
    return a


# ---------------------------------------------------------------------------
# resolve_nfc_id
# ---------------------------------------------------------------------------

def test_resolve_nfc_id_passthrough(db, test_user):
    assert resolve_nfc_id(db, test_user.id) == test_user.id


def test_resolve_nfc_id_follows_alias(db, test_user):
    _alias(db, ALIAS_ID, test_user.id)
    assert resolve_nfc_id(db, ALIAS_ID) == test_user.id


# ---------------------------------------------------------------------------
# Device endpoints transparently resolve aliases
# ---------------------------------------------------------------------------

def test_nfc_auth_via_alias_returns_main_tag(client, machine_token, test_user, db):
    _alias(db, ALIAS_ID, test_user.id)
    token, _ = machine_token
    resp = client.get(f"/api/v1/users/nfc/{ALIAS_ID}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == test_user.id
    assert resp.json()["balance"] == str(test_user.balance)


def test_purchase_via_alias_debits_main_account(client, checkout_token, db, test_user):
    from app.models.product import Product

    product = Product(ean="ALIAS111", name="Water", price=Decimal("1.00"), stock=5, category="drinks", active=True)
    db.add(product)
    db.commit()
    _alias(db, ALIAS_ID, test_user.id)

    token, _ = checkout_token
    balance_before = test_user.balance
    resp = client.post(
        f"/api/v1/products/{product.ean}/purchase",
        json={"nfc_id": ALIAS_ID},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db.refresh(test_user)
    assert test_user.balance == balance_before - product.price
    tx = db.query(Transaction).filter(
        Transaction.user_id == test_user.id, Transaction.type == TransactionType.purchase
    ).first()
    assert tx is not None


# ---------------------------------------------------------------------------
# Adding a tag as an alias merges its data onto the main tag
# ---------------------------------------------------------------------------

def test_do_transfer_merges_new_tag_onto_main_tag(db, test_user):
    """Simulates the 'add as alias' flow: _do_transfer(new_id, old_id) merges the
    new tag's data onto the existing main tag, keeping the main tag's id."""
    from app.api.v1.users import _do_transfer

    new_tag = User(id=UNLINKED_ID, name="Spare Tag", balance=Decimal("5.00"),
                    created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(new_tag)
    db.commit()
    main_balance = test_user.balance

    result = _do_transfer(UNLINKED_ID, test_user.id, db)
    db.add(UserCardAlias(alias_id=UNLINKED_ID, user_id=test_user.id,
                          created_at=datetime.now(UTC).replace(tzinfo=None)))
    db.commit()

    assert result.id == test_user.id
    assert result.balance == main_balance + Decimal("5.00")
    assert db.query(User).filter(User.id == UNLINKED_ID).first() is None
    assert resolve_nfc_id(db, UNLINKED_ID) == test_user.id


# ---------------------------------------------------------------------------
# Unlinking frees aliases
# ---------------------------------------------------------------------------

def test_unlink_oidc_frees_aliases(session_client, db, test_user):
    _alias(db, ALIAS_ID, test_user.id)

    resp = session_client.delete("/api/v1/users/me/oidc")
    assert resp.status_code == 200
    assert db.query(UserCardAlias).filter(UserCardAlias.alias_id == ALIAS_ID).first() is None


def test_get_and_unlink_me_aliases(session_client, db, test_user):
    _alias(db, ALIAS_ID, test_user.id)

    resp = session_client.get("/api/v1/users/me/aliases")
    assert resp.status_code == 200
    assert resp.json() == [ALIAS_ID]

    resp = session_client.delete("/api/v1/users/me/aliases")
    assert resp.status_code == 200
    assert db.query(UserCardAlias).filter(UserCardAlias.alias_id == ALIAS_ID).first() is None

    # Main tag/OIDC link stays intact
    db.refresh(test_user)
    assert test_user.oidc_sub == "test-oidc-sub"

    # Freed alias can be registered as a brand-new card again
    assert db.query(User).filter(User.id == ALIAS_ID).first() is None


def test_replace_flow_frees_old_tags_aliases(db, test_user):
    """Simulates the 'replace' flow's alias cleanup step from connect_transfer."""
    from app.api.v1.users import _do_transfer

    _alias(db, ALIAS_ID, test_user.id)
    new_tag = User(id=UNLINKED_ID, name=None, balance=Decimal("0.00"),
                    created_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(new_tag)
    db.commit()

    old_id = test_user.id
    db.query(UserCardAlias).filter(UserCardAlias.user_id == old_id).delete()
    result = _do_transfer(old_id, UNLINKED_ID, db)

    assert result.id == UNLINKED_ID
    assert db.query(UserCardAlias).filter(UserCardAlias.alias_id == ALIAS_ID).first() is None
    # The old main tag's row is gone too — free to register as a new card
    assert db.query(User).filter(User.id == old_id).first() is None


# ---------------------------------------------------------------------------
# Guards against nested/duplicate alias states
# ---------------------------------------------------------------------------

def test_connect_link_rejects_alias_tag(client, machine_token, test_user, db):
    _alias(db, ALIAS_ID, test_user.id)
    token, _ = machine_token
    resp = client.post(f"/api/v1/users/{ALIAS_ID}/connect-link", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409


def test_admin_transfer_rejects_alias_tag(admin_client, test_user, db):
    _alias(db, ALIAS_ID, test_user.id)
    resp = admin_client.post(
        f"/api/v1/users/{ALIAS_ID}/transfer", json={"new_id": UNLINKED_ID}
    )
    assert resp.status_code == 400

    resp = admin_client.post(
        f"/api/v1/users/{test_user.id}/transfer", json={"new_id": ALIAS_ID}
    )
    assert resp.status_code == 400
