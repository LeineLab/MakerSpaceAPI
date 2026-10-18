from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.models.machine import MachineAuthorization, MachineTermsAcceptance
from app.models.session import MachineSession  # noqa: F401


@pytest.fixture
def authorized(machine_token, test_user, db):
    token, machine = machine_token
    db.add(MachineAuthorization(
        machine_id=machine.id,
        user_id=test_user.id,
        price_per_login=Decimal("0.00"),
        price_per_minute=Decimal("0.10"),
        booking_interval=10,
        granted_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    db.commit()
    return token, machine


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _authorize(client, token, user_id):
    return client.get(f"/api/v1/machines/test-machine/authorize/{user_id}", headers=_hdr(token)).json()


def _session(client, token, user_id):
    return client.post("/api/v1/sessions", json={"nfc_id": user_id}, headers=_hdr(token))


def test_no_terms_means_no_restriction(client, authorized, test_user):
    token, _ = authorized
    assert _authorize(client, token, test_user.id)["authorized"] is True
    assert _session(client, token, test_user.id).status_code == 201


def test_terms_block_until_accepted(client, admin_client, authorized, test_user, db):
    token, _ = authorized
    test_user.oidc_sub = "test-admin-sub"
    db.commit()
    r = admin_client.post("/api/v1/machines/test-machine/terms", json={"text": "No liability"})
    assert r.status_code == 201
    assert r.json()["current"]["version"] == 1

    data = _authorize(client, token, test_user.id)
    assert data["authorized"] is False and data["terms_required"] is True
    balance = test_user.balance
    assert _session(client, token, test_user.id).status_code == 403
    db.refresh(test_user)
    assert test_user.balance == balance

    me = admin_client.get("/api/v1/users/me/machines").json()
    assert me[0]["terms_version"] == 1 and me[0]["terms_accepted"] is False

    assert admin_client.post("/api/v1/users/me/machines/test-machine/terms/accept", json={"version": 1}).status_code == 200
    assert admin_client.post("/api/v1/users/me/machines/test-machine/terms/accept", json={"version": 1}).status_code == 200
    assert db.query(MachineTermsAcceptance).count() == 1
    assert _authorize(client, token, test_user.id)["authorized"] is True
    assert _session(client, token, test_user.id).status_code == 201


def test_new_version_requires_reacceptance(client, admin_client, authorized, test_user, db):
    token, _ = authorized
    test_user.oidc_sub = "test-admin-sub"
    db.commit()
    admin_client.post("/api/v1/machines/test-machine/terms", json={"text": "v1"})
    admin_client.post("/api/v1/users/me/machines/test-machine/terms/accept", json={"version": 1})
    assert _authorize(client, token, test_user.id)["authorized"] is True

    admin_client.post("/api/v1/machines/test-machine/terms", json={"text": "v2"})
    assert _authorize(client, token, test_user.id)["authorized"] is False
    # accepting a stale version conflicts
    assert admin_client.post("/api/v1/users/me/machines/test-machine/terms/accept", json={"version": 1}).status_code == 409
    assert admin_client.post("/api/v1/users/me/machines/test-machine/terms/accept", json={"version": 2}).status_code == 200
    assert _authorize(client, token, test_user.id)["authorized"] is True
    assert db.query(MachineTermsAcceptance).count() == 2

    versions = admin_client.get("/api/v1/machines/test-machine/terms").json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]


def test_publish_requires_machine_manager(client, machine_token, treasurer_client):
    assert client.post("/api/v1/machines/test-machine/terms", json={"text": "x"}).status_code == 401
    assert treasurer_client.post("/api/v1/machines/test-machine/terms", json={"text": "x"}).status_code == 403


def test_accept_without_linked_card_404(admin_client, machine_token, db):
    admin_client.post("/api/v1/machines/test-machine/terms", json={"text": "x"})
    assert admin_client.post("/api/v1/users/me/machines/test-machine/terms/accept", json={"version": 1}).status_code == 404


def test_empty_terms_rejected(admin_client, machine_token):
    assert admin_client.post("/api/v1/machines/test-machine/terms", json={"text": "   "}).status_code == 400


def test_out_of_order_blocks_login_not_running_session(client, admin_client, authorized, test_user):
    token, _ = authorized
    sid = _session(client, token, test_user.id).json()["session_id"]
    r = admin_client.put("/api/v1/machines/test-machine/status", json={"out_of_order": True, "reason": "Laser tube"})
    assert r.status_code == 200 and r.json()["out_of_order"] is True
    data = _authorize(client, token, test_user.id)
    assert data["authorized"] is False and data["out_of_order"] is True
    assert data["out_of_order_reason"] == "Laser tube"
    assert client.put(f"/api/v1/sessions/{sid}", headers=_hdr(token)).status_code == 200
    client.delete(f"/api/v1/sessions/{sid}", headers=_hdr(token))
    assert _session(client, token, test_user.id).status_code == 403
    admin_client.put("/api/v1/machines/test-machine/status", json={"out_of_order": False})
    assert _authorize(client, token, test_user.id)["authorized"] is True
    assert _session(client, token, test_user.id).status_code == 201


def test_out_of_order_requires_machine_manager(machine_token, treasurer_client, client):
    assert client.put("/api/v1/machines/test-machine/status", json={"out_of_order": True}).status_code == 401
    assert treasurer_client.put("/api/v1/machines/test-machine/status", json={"out_of_order": True}).status_code == 403


def test_machine_health_endpoint(client, authorized, test_user):
    token, _ = authorized
    r = client.get("/api/health/machines/test-machine")
    assert r.status_code == 200 and r.json()["open_session_overdue_seconds"] == 0
    _session(client, token, test_user.id)
    assert client.get("/api/health/machines/test-machine").json()["open_session_overdue_seconds"] == 0
    assert client.get("/api/health/machines/nope").status_code == 404
