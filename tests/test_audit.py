import json

from app.models.audit import AuditLog


def _actions(db):
    return [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]


def test_machine_and_authorization_changes_are_logged(admin_client, db, test_user):
    admin_client.post("/api/v1/machines", json={"name": "Laser", "slug": "laser"})
    admin_client.put("/api/v1/machines/laser", json={"name": "Laser 2"})
    admin_client.post("/api/v1/machines/laser/authorizations", json={"nfc_id": test_user.id, "price_per_minute": "0.10"})
    admin_client.put(f"/api/v1/machines/laser/authorizations/{test_user.id}", json={"price_per_minute": "0.20"})
    admin_client.put("/api/v1/machines/laser/status", json={"out_of_order": True, "reason": "Rohr"})
    admin_client.delete(f"/api/v1/machines/laser/authorizations/{test_user.id}")
    assert _actions(db) == [
        "machine.created", "machine.updated", "machine.authorization_granted",
        "machine.authorization_updated", "machine.out_of_order", "machine.authorization_revoked",
    ]
    upd = db.query(AuditLog).filter(AuditLog.action == "machine.authorization_updated").one()
    detail = json.loads(upd.detail)
    assert detail["price_per_minute"] == {"from": "0.10", "to": "0.20"}
    assert upd.actor == "test-admin-sub" and upd.target_id == "laser"


def test_unchanged_update_not_logged(admin_client, db, machine_token):
    admin_client.put("/api/v1/machines/test-machine", json={"name": "Test Machine"})
    assert _actions(db) == []


def test_audit_endpoint_admin_only_and_filters(admin_client, treasurer_client, db, machine_token):
    admin_client.put("/api/v1/machines/test-machine/status", json={"out_of_order": True})
    admin_client.post("/api/v1/bankomat/targets", json={"name": "Kasse", "slug": "kasse"})
    assert treasurer_client.get("/api/v1/audit").status_code == 403
    r = admin_client.get("/api/v1/audit?action=machine.")
    assert r.status_code == 200 and [x["action"] for x in r.json()] == ["machine.out_of_order"]
    assert r.headers["X-Total-Count"] == "1"
    assert admin_client.get("/api/v1/audit").headers["X-Total-Count"] == "2"
    assert len(admin_client.get("/api/v1/audit?target_id=kasse").json()) == 1


def test_product_audit_overview(product_manager_client, db):
    c = product_manager_client
    c.post("/api/v1/categories", json={"name": "Drinks"})
    c.post("/api/v1/products", json={"ean": "111", "name": "Cola", "price": "1.50", "stock": 5, "category": "Drinks"})
    c.put("/api/v1/products/111", json={"price": "1.80"})
    c.post("/api/v1/products/111/stock", json={"delta": 3, "reason": "Delivery"})
    r = c.get("/api/v1/products/audit")
    assert r.status_code == 200
    types = [x["change_type"] for x in r.json()]
    assert "price_change" in types and "stock_add" in types
    assert r.json()[0]["product_name"] == "Cola" and r.json()[0]["product_ean"] == "111"
    assert len(c.get("/api/v1/products/audit?change_type=price_change").json()) == 1
    assert len(c.get("/api/v1/products/audit?q=cola").json()) == len(types)
    assert c.get("/api/v1/products/audit?q=nothing").json() == []
    assert "product.category_created" in _actions(db)


def test_audit_pages_render(admin_client, product_manager_client):
    assert admin_client.get("/audit").status_code == 200
    assert product_manager_client.get("/products/changes").status_code == 200
