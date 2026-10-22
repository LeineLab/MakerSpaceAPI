from datetime import UTC, datetime, timedelta

import pytest

from app.models.audit import AuditLog

API = "/api/v1/inventory"


def _in_days(n: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=n)).isoformat()


@pytest.fixture
def c(treasurer_client):
    """Any logged-in user: inventory has no role gate."""
    return treasurer_client


@pytest.fixture
def location(c):
    return c.post(f"{API}/locations", json={"name": "Erste-Hilfe-Kasten Werkstatt"}).json()


def _item(c, location, **kw):
    body = {"location_id": location["id"], "name": "Litze 0,14 grün", "target_qty": 10, **kw}
    resp = c.post(f"{API}/items", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- auth -------------------------------------------------------------------

def test_requires_login(client):
    for path in ("/locations", "/items", "/shopping-list", "/summary"):
        assert client.get(f"{API}{path}").status_code == 401
    assert client.post(f"{API}/locations", json={"name": "x"}).status_code == 401


# --- locations --------------------------------------------------------------

def test_location_slug_is_ascii_and_stable_on_rename(c, location):
    assert location["slug"] == "erste-hilfe-kasten-werkstatt"
    resp = c.put(f"{API}/locations/{location['id']}", json={"name": "Werkstatt Verbandkasten"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Werkstatt Verbandkasten"
    assert resp.json()["slug"] == "erste-hilfe-kasten-werkstatt"


def test_location_slug_transliterates_umlauts_and_stays_unique(c):
    a = c.post(f"{API}/locations", json={"name": "Lötplatz"}).json()
    b = c.post(f"{API}/locations", json={"name": "Loetplatz"}).json()
    assert a["slug"] == "loetplatz"
    assert b["slug"] == "loetplatz-2"


def test_duplicate_location_name_conflicts(c, location):
    resp = c.post(f"{API}/locations", json={"name": location["name"]})
    assert resp.status_code == 409


def test_delete_location_blocked_while_it_has_items(c, location):
    item = _item(c, location)
    assert c.delete(f"{API}/locations/{location['id']}").status_code == 409
    assert c.delete(f"{API}/items/{item['id']}").status_code == 200
    assert c.delete(f"{API}/locations/{location['id']}").status_code == 200


def test_mark_location_checked(c, location):
    resp = c.post(f"{API}/locations/{location['id']}/check")
    assert resp.status_code == 200
    assert resp.json()["last_checked_at"] is not None
    assert resp.json()["last_checked_by"] == "test-treasurer-sub"


# --- untracked items --------------------------------------------------------

def test_untracked_item_count_and_to_order(c, location):
    item = _item(c, location, quantity=4)
    assert item["on_hand"] == 4
    assert item["to_order"] == 6
    assert item["batches"] == []


def test_quantity_delta_and_absolute(c, location):
    item = _item(c, location, quantity=4)
    r = c.post(f"{API}/items/{item['id']}/quantity", json={"delta": 3}).json()
    assert r["on_hand"] == 7
    r = c.post(f"{API}/items/{item['id']}/quantity", json={"quantity": 10}).json()
    assert r["on_hand"] == 10
    assert r["to_order"] == 0


def test_quantity_cannot_go_negative(c, location):
    item = _item(c, location, quantity=1)
    assert c.post(f"{API}/items/{item['id']}/quantity", json={"delta": -2}).status_code == 400


def test_quantity_requires_exactly_one_of_quantity_or_delta(c, location):
    item = _item(c, location)
    assert c.post(f"{API}/items/{item['id']}/quantity", json={}).status_code == 422
    assert c.post(f"{API}/items/{item['id']}/quantity", json={"quantity": 1, "delta": 1}).status_code == 422


def test_duplicate_item_name_in_same_location_conflicts(c, location):
    _item(c, location)
    resp = c.post(f"{API}/items", json={"location_id": location["id"], "name": "Litze 0,14 grün"})
    assert resp.status_code == 409


# --- tracked items / batches ------------------------------------------------

def test_tracked_item_count_is_sum_of_batches(c, location):
    item = _item(c, location, name="Wundverband", track_expiry=True, target_qty=5)
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 2, "expires_on": _in_days(400)})
    r = c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 3, "expires_on": _in_days(800)}).json()
    assert r["on_hand"] == 5
    assert r["to_order"] == 0
    assert len(r["batches"]) == 2


def test_quantity_endpoint_rejected_for_tracked_item(c, location):
    item = _item(c, location, track_expiry=True)
    assert c.post(f"{API}/items/{item['id']}/quantity", json={"delta": 1}).status_code == 400


def test_batches_rejected_for_untracked_item(c, location):
    item = _item(c, location)
    assert c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 1}).status_code == 400


def test_batch_with_same_date_is_merged(c, location):
    item = _item(c, location, track_expiry=True)
    d = _in_days(300)
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 2, "expires_on": d})
    r = c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 3, "expires_on": d}).json()
    assert len(r["batches"]) == 1
    assert r["batches"][0]["quantity"] == 5


def test_expired_and_expiring_batches(c, location):
    item = _item(c, location, name="Augenspülung", track_expiry=True, target_qty=4)
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 1, "expires_on": _in_days(-5)})
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 1, "expires_on": _in_days(10)})
    r = c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 2, "expires_on": _in_days(500)}).json()
    assert r["on_hand"] == 4
    assert r["expired_qty"] == 1
    assert r["expiring_soon_qty"] == 1
    assert r["usable_qty"] == 3
    # target 4, only the 2 batches beyond the warn window really count
    assert r["to_order"] == 2
    states = {b["quantity"]: b["state"] for b in r["batches"]}
    assert sorted(b["state"] for b in r["batches"]) == ["expired", "expiring", "ok"]
    assert states[2] == "ok"


def test_batch_without_date_is_never_expired(c, location):
    item = _item(c, location, track_expiry=True, target_qty=2)
    r = c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 2}).json()
    assert r["expired_qty"] == 0
    assert r["to_order"] == 0


def test_update_batch_quantity_zero_removes_it(c, location):
    item = _item(c, location, track_expiry=True)
    r = c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 2, "expires_on": _in_days(300)}).json()
    batch_id = r["batches"][0]["id"]
    r = c.put(f"{API}/batches/{batch_id}", json={"quantity": 0}).json()
    assert r["batches"] == []
    assert r["on_hand"] == 0


def test_update_batch_date_conflict(c, location):
    item = _item(c, location, track_expiry=True)
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 1, "expires_on": _in_days(300)})
    r = c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 1, "expires_on": _in_days(400)}).json()
    other = [b for b in r["batches"] if b["expires_on"] == _in_days(400)][0]
    resp = c.put(f"{API}/batches/{other['id']}", json={"expires_on": _in_days(300)})
    assert resp.status_code == 409


def test_delete_batch(c, location):
    item = _item(c, location, track_expiry=True)
    r = c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 2, "expires_on": _in_days(300)}).json()
    r = c.delete(f"{API}/batches/{r['batches'][0]['id']}").json()
    assert r["batches"] == []


# --- switching track_expiry -------------------------------------------------

def test_enable_tracking_turns_count_into_undated_batch(c, location):
    item = _item(c, location, quantity=6)
    r = c.put(f"{API}/items/{item['id']}", json={"track_expiry": True}).json()
    assert r["track_expiry"] is True
    assert r["on_hand"] == 6
    assert len(r["batches"]) == 1
    assert r["batches"][0]["expires_on"] is None


def test_disable_tracking_sums_batches_into_count(c, location):
    item = _item(c, location, track_expiry=True)
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 2, "expires_on": _in_days(300)})
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 3, "expires_on": _in_days(600)})
    r = c.put(f"{API}/items/{item['id']}", json={"track_expiry": False}).json()
    assert r["track_expiry"] is False
    assert r["on_hand"] == 5
    assert r["batches"] == []


def test_create_tracked_item_with_initial_batch(c, location):
    item = _item(c, location, track_expiry=True, quantity=3, expires_on=_in_days(200))
    assert item["on_hand"] == 3
    assert item["batches"][0]["expires_on"] == _in_days(200)


def test_move_item_to_other_location(c, location):
    other = c.post(f"{API}/locations", json={"name": "Lötplatz"}).json()
    item = _item(c, location)
    r = c.put(f"{API}/items/{item['id']}", json={"location_id": other["id"]}).json()
    assert r["location_id"] == other["id"]
    assert c.put(f"{API}/items/{item['id']}", json={"location_id": 9999}).status_code == 404


def test_list_items_filtered_by_location(c, location):
    other = c.post(f"{API}/locations", json={"name": "Lötplatz"}).json()
    _item(c, location, name="A")
    _item(c, other, name="B")
    names = [i["name"] for i in c.get(f"{API}/items", params={"location_id": other["id"]}).json()]
    assert names == ["B"]


# --- shopping list / summary ------------------------------------------------

def test_shopping_list_contains_only_items_needing_attention(c, location):
    _item(c, location, name="Voll", target_qty=3, quantity=3)
    _item(c, location, name="Leer", target_qty=5, quantity=1)
    expired = _item(c, location, name="Abgelaufen", target_qty=1, track_expiry=True)
    c.post(f"{API}/items/{expired['id']}/batches", json={"quantity": 1, "expires_on": _in_days(-1)})
    rows = c.get(f"{API}/shopping-list").json()
    assert [r["item"]["name"] for r in rows] == ["Abgelaufen", "Leer"]
    assert rows[1]["item"]["to_order"] == 4
    assert rows[1]["location_slug"] == location["slug"]
    assert c.get(f"{API}/summary").json() == {"attention_items": 2}


def test_expired_stock_stays_on_list_even_when_target_is_covered(c, location):
    item = _item(c, location, name="Pflaster", target_qty=1, track_expiry=True)
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 1, "expires_on": _in_days(500)})
    c.post(f"{API}/items/{item['id']}/batches", json={"quantity": 1, "expires_on": _in_days(-3)})
    rows = c.get(f"{API}/shopping-list").json()
    assert len(rows) == 1
    assert rows[0]["item"]["to_order"] == 0
    assert rows[0]["item"]["expired_qty"] == 1


def test_target_zero_is_never_short(c, location):
    _item(c, location, target_qty=0, quantity=0)
    assert c.get(f"{API}/shopping-list").json() == []


# --- audit ------------------------------------------------------------------

def test_changes_are_audited_with_acting_user(c, location, db):
    item = _item(c, location, quantity=2)
    c.post(f"{API}/items/{item['id']}/quantity", json={"delta": 1})
    actions = {a.action: a for a in db.query(AuditLog).filter(AuditLog.action.like("inventory.%")).all()}
    assert {"inventory.location.created", "inventory.item.created", "inventory.item.quantity"} <= set(actions)
    assert actions["inventory.item.quantity"].actor == "test-treasurer-sub"


# --- web page ---------------------------------------------------------------

def test_inventory_page_requires_login(client):
    assert client.get("/inventory").status_code == 401


def test_inventory_page_renders_for_any_user_and_passes_slug(c, location):
    resp = c.get(f"/inventory/{location['slug']}")
    assert resp.status_code == 200
    # The slug must live in a <script> constant: a tojson string inside the
    # double-quoted x-data attribute would end the attribute early.
    assert f'const INITIAL_SLUG = "{location["slug"]}";' in resp.text
    assert 'x-data="inventoryApp(INITIAL_SLUG)"' in resp.text
    assert c.get("/inventory").status_code == 200
