from decimal import Decimal
from datetime import UTC, datetime


def test_authorize_user_not_found(client, machine_token):
    token, machine = machine_token
    resp = client.get(
        f"/api/v1/machines/{machine.slug}/authorize/999999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_authorize_user_not_authorized(client, machine_token, test_user):
    token, machine = machine_token
    resp = client.get(
        f"/api/v1/machines/{machine.slug}/authorize/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["authorized"] is False
    assert data["user_id"] == test_user.id
    assert data["user_name"] == test_user.name
    assert Decimal(str(data["balance"])) == test_user.balance
    assert data["booking_interval"] == 0


def test_authorize_user_authorized(client, machine_token, test_user, db):
    from app.models.machine import MachineAuthorization

    token, machine = machine_token
    auth = MachineAuthorization(
        machine_id=machine.id,
        user_id=test_user.id,
        price_per_login=Decimal("1.00"),
        price_per_minute=Decimal("0.05"),
        booking_interval=30,
        granted_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(auth)
    db.commit()

    resp = client.get(
        f"/api/v1/machines/{machine.slug}/authorize/{test_user.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["authorized"] is True
    assert data["price_per_login"] == "1.00"
    assert data["price_per_minute"] == "0.05"
    assert data["booking_interval"] == 30


def test_authorize_wrong_machine_slug(client, machine_token, test_user):
    """A device may not query authorisations on a different machine's slug."""
    token, machine = machine_token
    resp = client.get(
        "/api/v1/machines/other-machine/authorize/999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_authorize_requires_device_token(client, machine_token, test_user):
    _, machine = machine_token
    resp = client.get(
        f"/api/v1/machines/{machine.slug}/authorize/{test_user.id}",
    )
    assert resp.status_code == 401
