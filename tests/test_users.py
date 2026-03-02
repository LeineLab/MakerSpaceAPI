from datetime import UTC, datetime, timedelta
from decimal import Decimal

from passlib.context import CryptContext

from app.models.user import User

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# GET /users/nfc/{nfc_id} — device token
# ---------------------------------------------------------------------------

def test_nfc_auth_success(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/users/nfc/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == test_user.id
    assert data["name"] == test_user.name
    assert Decimal(str(data["balance"])) == test_user.balance


def test_nfc_auth_not_found(client, machine_token):
    token, _ = machine_token
    assert client.get(
        "/api/v1/users/nfc/999999",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 404


def test_nfc_auth_requires_device_token(client, test_user):
    assert client.get(f"/api/v1/users/nfc/{test_user.id}").status_code == 401


def test_nfc_auth_invalid_token(client, test_user):
    assert client.get(
        f"/api/v1/users/nfc/{test_user.id}",
        headers={"Authorization": "Bearer bad-token"},
    ).status_code == 401


def test_nfc_auth_has_pin_false(client, machine_token, test_user):
    """Device NFC lookup returns has_pin=false when no PIN is set."""
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/users/nfc/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["has_pin"] is False


def test_nfc_auth_has_pin_true(client, machine_token, test_user, db):
    """Device NFC lookup returns has_pin=true when a PIN is set."""
    test_user.pin_hash = _pwd.hash("secret")
    db.commit()
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/users/nfc/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["has_pin"] is True


# ---------------------------------------------------------------------------
# POST /users — checkout device only
# ---------------------------------------------------------------------------

def test_create_user_success(client, checkout_token, db):
    token, _ = checkout_token
    resp = client.post(
        "/api/v1/users",
        json={"id": 111222333, "name": "New Member"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == 111222333
    assert data["name"] == "New Member"
    assert Decimal(str(data["balance"])) == Decimal("0.00")
    assert db.query(User).filter(User.id == 111222333).first() is not None


def test_create_user_without_name(client, checkout_token):
    token, _ = checkout_token
    resp = client.post(
        "/api/v1/users",
        json={"id": 444555666},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] is None


def test_create_user_duplicate_nfc_id(client, checkout_token, test_user):
    token, _ = checkout_token
    resp = client.post(
        "/api/v1/users",
        json={"id": test_user.id, "name": "Duplicate"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


def test_create_user_requires_checkout_device(client, machine_token):
    """Regular machine (type='machine') must be rejected."""
    token, _ = machine_token
    assert client.post(
        "/api/v1/users",
        json={"id": 777888999, "name": "X"},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 403


def test_create_user_requires_token(client):
    assert client.post("/api/v1/users", json={"id": 777888999, "name": "X"}).status_code == 401


# ---------------------------------------------------------------------------
# GET /users — admin only
# ---------------------------------------------------------------------------

def test_list_users_empty(admin_client):
    assert admin_client.get("/api/v1/users").json() == []


def test_list_users(admin_client, test_user):
    data = admin_client.get("/api/v1/users").json()
    assert len(data) == 1
    assert data[0]["id"] == test_user.id


def test_list_users_ordered_by_created_at(admin_client, db):
    now = datetime.now(UTC).replace(tzinfo=None)
    db.add_all([
        User(id=100, name="Later",   balance=Decimal("0"), created_at=now),
        User(id=200, name="Earlier", balance=Decimal("0"), created_at=now - timedelta(hours=1)),
    ])
    db.commit()

    data = admin_client.get("/api/v1/users").json()
    assert data[0]["id"] == 200  # earliest created_at first
    assert data[1]["id"] == 100


def test_list_users_requires_admin(client):
    assert client.get("/api/v1/users").status_code == 401


# ---------------------------------------------------------------------------
# GET /users/{nfc_id} — admin only
# ---------------------------------------------------------------------------

def test_get_user_success(admin_client, test_user):
    resp = admin_client.get(f"/api/v1/users/{test_user.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == test_user.id
    assert data["oidc_sub"] is None


def test_get_user_has_pin_field(admin_client, test_user):
    """Admin GET /users/{nfc_id} includes has_pin field."""
    resp = admin_client.get(f"/api/v1/users/{test_user.id}")
    assert resp.status_code == 200
    assert "has_pin" in resp.json()
    assert resp.json()["has_pin"] is False


def test_get_user_not_found(admin_client):
    assert admin_client.get("/api/v1/users/999999").status_code == 404


def test_get_user_requires_admin(client, test_user):
    assert client.get(f"/api/v1/users/{test_user.id}").status_code == 401


# ---------------------------------------------------------------------------
# PUT /users/{nfc_id}/oidc — admin only
# ---------------------------------------------------------------------------

def test_link_oidc_success(admin_client, test_user, db):
    resp = admin_client.put(
        f"/api/v1/users/{test_user.id}/oidc",
        json={"oidc_sub": "auth0|abc123"},
    )
    assert resp.status_code == 200
    assert resp.json()["oidc_sub"] == "auth0|abc123"

    db.refresh(test_user)
    assert test_user.oidc_sub == "auth0|abc123"


def test_link_oidc_replaces_existing(admin_client, test_user, db):
    test_user.oidc_sub = "old-sub"
    db.commit()

    resp = admin_client.put(
        f"/api/v1/users/{test_user.id}/oidc",
        json={"oidc_sub": "new-sub"},
    )
    assert resp.status_code == 200
    assert resp.json()["oidc_sub"] == "new-sub"


def test_link_oidc_missing_sub(admin_client, test_user):
    resp = admin_client.put(f"/api/v1/users/{test_user.id}/oidc", json={})
    assert resp.status_code == 422  # Pydantic validation: oidc_sub is required


def test_link_oidc_sub_already_taken(admin_client, test_user, db):
    """The same oidc_sub cannot be linked to two different users."""
    db.add(User(id=999000111, name="Other", balance=Decimal("0.00"), oidc_sub="taken-sub"))
    db.commit()

    resp = admin_client.put(
        f"/api/v1/users/{test_user.id}/oidc",
        json={"oidc_sub": "taken-sub"},
    )
    assert resp.status_code == 409


def test_link_oidc_user_not_found(admin_client):
    assert admin_client.put(
        "/api/v1/users/999999/oidc",
        json={"oidc_sub": "auth0|xyz"},
    ).status_code == 404


def test_link_oidc_requires_admin(client, test_user):
    assert client.put(
        f"/api/v1/users/{test_user.id}/oidc",
        json={"oidc_sub": "auth0|abc"},
    ).status_code == 401


# ---------------------------------------------------------------------------
# POST /users/{nfc_id}/connect-link — device token
# ---------------------------------------------------------------------------

def test_generate_connect_link_success(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.post(
        f"/api/v1/users/{test_user.id}/connect-link",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data
    assert "/auth/connect/" in data["url"]


def test_generate_connect_link_already_linked(client, machine_token, test_user, db):
    test_user.oidc_sub = "already|linked"
    db.commit()
    token, _ = machine_token
    resp = client.post(
        f"/api/v1/users/{test_user.id}/connect-link",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


def test_generate_connect_link_user_not_found(client, machine_token):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/users/999999/connect-link",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_generate_connect_link_requires_device_token(client, test_user):
    assert client.post(f"/api/v1/users/{test_user.id}/connect-link").status_code == 401
