from datetime import UTC, datetime

import pytest

from app.models.rental import Rental, RentalItem, RentalPermission
from app.models.user import User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def item(db):
    """An active rental item."""
    i = RentalItem(
        name="Laser Cutter",
        description="CO2 laser",
        uhf_tid="AABBCCDD1122",
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(i)
    db.commit()
    return i


@pytest.fixture
def permitted_user(test_user, db):
    """test_user with rental permission granted."""
    perm = RentalPermission(
        user_id=test_user.id,
        granted_by="test-admin-sub",
        granted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(perm)
    db.commit()
    return test_user


@pytest.fixture
def active_rental(item, permitted_user, db):
    """An active rental for permitted_user on item."""
    r = Rental(item_id=item.id, user_id=permitted_user.id, rented_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(r)
    db.commit()
    return r


# ---------------------------------------------------------------------------
# GET /rentals/items — admin only
# ---------------------------------------------------------------------------

def test_list_items_empty(admin_client):
    resp = admin_client.get("/api/v1/rentals/items")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_items(admin_client, item):
    resp = admin_client.get("/api/v1/rentals/items")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["uhf_tid"] == "AABBCCDD1122"
    assert data[0]["active"] is True


def test_list_items_requires_admin(client):
    resp = client.get("/api/v1/rentals/items")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /rentals/items — admin only
# ---------------------------------------------------------------------------

def test_create_item_success(admin_client):
    resp = admin_client.post(
        "/api/v1/rentals/items",
        json={"name": "3D Printer", "uhf_tid": "deadbeef0001"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["uhf_tid"] == "DEADBEEF0001"  # normalised to upper-case
    assert data["active"] is True


def test_create_item_tid_uppercased(admin_client):
    resp = admin_client.post(
        "/api/v1/rentals/items",
        json={"name": "Soldering Station", "uhf_tid": "aabbcc001122"},
    )
    assert resp.status_code == 201
    assert resp.json()["uhf_tid"] == "AABBCC001122"


def test_create_item_duplicate_tid(admin_client, item):
    resp = admin_client.post(
        "/api/v1/rentals/items",
        json={"name": "Duplicate", "uhf_tid": "aabbccdd1122"},  # same as fixture (case-insensitive)
    )
    assert resp.status_code == 409


def test_create_item_requires_admin(client):
    resp = client.post(
        "/api/v1/rentals/items",
        json={"name": "Item", "uhf_tid": "000000000001"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /rentals/items/{item_id} — admin only
# ---------------------------------------------------------------------------

def test_update_item_success(admin_client, item):
    resp = admin_client.put(
        f"/api/v1/rentals/items/{item.id}",
        json={"name": "Updated Name", "active": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated Name"
    assert data["active"] is False


def test_update_item_not_found(admin_client):
    resp = admin_client.put(
        "/api/v1/rentals/items/999999",
        json={"name": "X"},
    )
    assert resp.status_code == 404


def test_update_item_requires_admin(client, item):
    resp = client.put(f"/api/v1/rentals/items/{item.id}", json={"name": "X"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /rentals/items/{uhf_tid}/status — device token
# ---------------------------------------------------------------------------

def test_item_status_not_rented(client, machine_token, item):
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/rentals/items/{item.uhf_tid}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_rented"] is False
    assert data["rental_id"] is None
    assert data["rented_by_user_id"] is None


def test_item_status_is_rented(client, machine_token, active_rental, item, permitted_user):
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/rentals/items/{item.uhf_tid}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_rented"] is True
    assert data["rental_id"] == active_rental.id
    assert data["rented_by_user_id"] == permitted_user.id
    assert data["rented_by_name"] == permitted_user.name


def test_item_status_tid_case_insensitive(client, machine_token, item):
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/rentals/items/{item.uhf_tid.lower()}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_item_status_not_found(client, machine_token):
    token, _ = machine_token
    resp = client.get(
        "/api/v1/rentals/items/FFFF0000DEAD/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_item_status_requires_device_token(client, item):
    resp = client.get(f"/api/v1/rentals/items/{item.uhf_tid}/status")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /rentals/authorize/{nfc_id} — device token
# ---------------------------------------------------------------------------

def test_authorize_renter_no_permission(client, machine_token, test_user):
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/rentals/authorize/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["authorized"] is False
    assert data["user_id"] == test_user.id
    assert data["user_name"] == test_user.name


def test_authorize_renter_with_permission(client, machine_token, permitted_user):
    token, _ = machine_token
    resp = client.get(
        f"/api/v1/rentals/authorize/{permitted_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["authorized"] is True


def test_authorize_renter_user_not_found(client, machine_token):
    token, _ = machine_token
    resp = client.get(
        "/api/v1/rentals/authorize/999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_authorize_renter_requires_device_token(client, test_user):
    resp = client.get(f"/api/v1/rentals/authorize/{test_user.id}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /rentals — rent an item
# ---------------------------------------------------------------------------

def test_rent_item_success(client, machine_token, permitted_user, item, db):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/rentals",
        json={"nfc_id": permitted_user.id, "uhf_tid": item.uhf_tid},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["item_id"] == item.id
    assert data["user_id"] == permitted_user.id
    assert data["returned_at"] is None


def test_rent_item_user_not_found(client, machine_token, item):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/rentals",
        json={"nfc_id": 999999, "uhf_tid": item.uhf_tid},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_rent_item_no_permission(client, machine_token, test_user, item):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/rentals",
        json={"nfc_id": test_user.id, "uhf_tid": item.uhf_tid},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_rent_item_not_found(client, machine_token, permitted_user):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/rentals",
        json={"nfc_id": permitted_user.id, "uhf_tid": "FFFF0000DEAD"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_rent_item_inactive(client, machine_token, permitted_user, item, db):
    token, _ = machine_token
    item.active = False
    db.commit()
    resp = client.post(
        "/api/v1/rentals",
        json={"nfc_id": permitted_user.id, "uhf_tid": item.uhf_tid},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_rent_item_already_rented(client, machine_token, permitted_user, item, active_rental):
    token, _ = machine_token
    resp = client.post(
        "/api/v1/rentals",
        json={"nfc_id": permitted_user.id, "uhf_tid": item.uhf_tid},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


def test_rent_item_requires_device_token(client, permitted_user, item):
    resp = client.post(
        "/api/v1/rentals",
        json={"nfc_id": permitted_user.id, "uhf_tid": item.uhf_tid},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /rentals/{rental_id} — return an item
# ---------------------------------------------------------------------------

def test_return_item_success(client, machine_token, active_rental, db):
    token, _ = machine_token
    resp = client.delete(
        f"/api/v1/rentals/{active_rental.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db.refresh(active_rental)
    assert active_rental.returned_at is not None


def test_return_item_already_returned(client, machine_token, active_rental, db):
    """Returning an already-returned rental is idempotent (200 with message)."""
    token, _ = machine_token
    active_rental.returned_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()

    resp = client.delete(
        f"/api/v1/rentals/{active_rental.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "already" in resp.json()["detail"].lower()


def test_return_item_not_found(client, machine_token):
    token, _ = machine_token
    resp = client.delete(
        "/api/v1/rentals/999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_return_item_requires_device_token(client, active_rental):
    resp = client.delete(f"/api/v1/rentals/{active_rental.id}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /rentals/active — admin only
# ---------------------------------------------------------------------------

def test_list_active_rentals_empty(admin_client):
    resp = admin_client.get("/api/v1/rentals/active")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_active_rentals(admin_client, active_rental, item, permitted_user):
    resp = admin_client.get("/api/v1/rentals/active")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["rental_id"] == active_rental.id
    assert data[0]["item_name"] == item.name
    assert data[0]["uhf_tid"] == item.uhf_tid
    assert data[0]["user_name"] == permitted_user.name


def test_list_active_rentals_excludes_returned(admin_client, active_rental, db):
    active_rental.returned_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()

    resp = admin_client.get("/api/v1/rentals/active")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_active_rentals_requires_admin(client):
    resp = client.get("/api/v1/rentals/active")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /rentals/permissions — admin only
# ---------------------------------------------------------------------------

def test_list_permissions_empty(admin_client):
    resp = admin_client.get("/api/v1/rentals/permissions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_permissions(admin_client, permitted_user):
    resp = admin_client.get("/api/v1/rentals/permissions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["user_id"] == permitted_user.id
    assert data[0]["user_name"] == permitted_user.name


def test_list_permissions_requires_admin(client):
    resp = client.get("/api/v1/rentals/permissions")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /rentals/permissions/{nfc_id} — admin only
# ---------------------------------------------------------------------------

def test_grant_permission_success(admin_client, test_user):
    resp = admin_client.post(f"/api/v1/rentals/permissions/{test_user.id}")
    assert resp.status_code == 201
    data = resp.json()
    assert data["user_id"] == test_user.id
    assert data["user_name"] == test_user.name
    assert data["granted_by"] == "test-admin-sub"


def test_grant_permission_user_not_found(admin_client):
    resp = admin_client.post("/api/v1/rentals/permissions/999999")
    assert resp.status_code == 404


def test_grant_permission_duplicate(admin_client, permitted_user):
    resp = admin_client.post(f"/api/v1/rentals/permissions/{permitted_user.id}")
    assert resp.status_code == 409


def test_grant_permission_requires_admin(client, test_user):
    resp = client.post(f"/api/v1/rentals/permissions/{test_user.id}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /rentals/permissions/{nfc_id} — admin only
# ---------------------------------------------------------------------------

def test_revoke_permission_success(admin_client, permitted_user, db):
    resp = admin_client.delete(f"/api/v1/rentals/permissions/{permitted_user.id}")
    assert resp.status_code == 200

    from app.models.rental import RentalPermission
    assert db.query(RentalPermission).filter(RentalPermission.user_id == permitted_user.id).first() is None


def test_revoke_permission_not_found(admin_client):
    resp = admin_client.delete("/api/v1/rentals/permissions/999999")
    assert resp.status_code == 404


def test_revoke_permission_requires_admin(client, permitted_user):
    resp = client.delete(f"/api/v1/rentals/permissions/{permitted_user.id}")
    assert resp.status_code == 401
