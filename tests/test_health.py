from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_db_unreachable(client):
    with patch("app.main.Session.execute", side_effect=OperationalError("x", {}, None)):
        resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "database": "unreachable"}
