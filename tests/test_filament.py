import pytest

from app.models.filament import FilamentBrand, FilamentRoll, FilamentType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def brand(db):
    b = FilamentBrand(name="Creality")
    db.add(b)
    db.commit()
    return b


@pytest.fixture
def ftype(db):
    t = FilamentType(name="Hyper PLA RFID")
    db.add(t)
    db.commit()
    return t


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_brands_readable_by_any_session_user(treasurer_client):
    """GET endpoints only require a logged-in session, not product-manager —
    write endpoints stay product-manager-only (see CLAUDE.md #74)."""
    resp = treasurer_client.get("/api/v1/filament/brands")
    assert resp.status_code == 200


def test_types_readable_by_any_session_user(treasurer_client):
    resp = treasurer_client.get("/api/v1/filament/types")
    assert resp.status_code == 200


def test_rolls_readable_by_any_session_user(treasurer_client):
    resp = treasurer_client.get("/api/v1/filament/rolls")
    assert resp.status_code == 200


def test_rolls_summary_is_fully_public(client):
    """No auth at all — see CLAUDE.md #75. Unlike GET /rolls, this response
    carries no per-roll identifying detail (vendor_serial, added_by/removed_by,
    timestamps), so it's safe to expose with zero auth."""
    resp = client.get("/api/v1/filament/rolls/summary")
    assert resp.status_code == 200


def test_create_brand_requires_product_manager(treasurer_client):
    resp = treasurer_client.post("/api/v1/filament/brands", json={"name": "Should Fail"})
    assert resp.status_code == 403


def test_create_roll_requires_product_manager(treasurer_client, brand, ftype):
    resp = treasurer_client.post(
        "/api/v1/filament/rolls",
        json={"brand_id": brand.id, "type_id": ftype.id, "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 403


def test_admin_passes_product_manager_check(admin_client):
    resp = admin_client.get("/api/v1/filament/brands")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Brands / Types CRUD
# ---------------------------------------------------------------------------

def test_create_and_list_brand(product_manager_client):
    resp = product_manager_client.post("/api/v1/filament/brands", json={"name": "Bambu Lab"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Bambu Lab"

    resp = product_manager_client.get("/api/v1/filament/brands")
    assert resp.status_code == 200
    assert [b["name"] for b in resp.json()] == ["Bambu Lab"]


def test_create_duplicate_brand_conflicts(product_manager_client, brand):
    resp = product_manager_client.post("/api/v1/filament/brands", json={"name": "Creality"})
    assert resp.status_code == 409


def test_delete_brand(product_manager_client, brand):
    resp = product_manager_client.delete(f"/api/v1/filament/brands/{brand.id}")
    assert resp.status_code == 200


def test_delete_brand_in_use_conflicts(product_manager_client, brand, ftype, db):
    db.add(FilamentRoll(brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz", added_by="pm"))
    db.commit()
    resp = product_manager_client.delete(f"/api/v1/filament/brands/{brand.id}")
    assert resp.status_code == 409


def test_delete_unknown_brand_404(product_manager_client):
    resp = product_manager_client.delete("/api/v1/filament/brands/999")
    assert resp.status_code == 404


def test_create_and_delete_type(product_manager_client):
    resp = product_manager_client.post("/api/v1/filament/types", json={"name": "PETG"})
    assert resp.status_code == 201
    type_id = resp.json()["id"]

    resp = product_manager_client.delete(f"/api/v1/filament/types/{type_id}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Manual roll add/list/remove/restore (web UI)
# ---------------------------------------------------------------------------

def test_create_roll(product_manager_client, brand, ftype):
    resp = product_manager_client.post(
        "/api/v1/filament/rolls",
        json={"brand_id": brand.id, "type_id": ftype.id, "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 201
    rolls = resp.json()
    assert len(rolls) == 1
    assert rolls[0]["brand_name"] == "Creality"
    assert rolls[0]["type_name"] == "Hyper PLA RFID"
    assert rolls[0]["in_stock"] is True
    assert rolls[0]["vendor_serial"] is None


def test_create_roll_bulk_quantity(product_manager_client, brand, ftype):
    resp = product_manager_client.post(
        "/api/v1/filament/rolls",
        json={"brand_id": brand.id, "type_id": ftype.id, "weight_grams": 1000, "color": "Schwarz", "quantity": 5},
    )
    assert resp.status_code == 201
    assert len(resp.json()) == 5

    resp = product_manager_client.get("/api/v1/filament/rolls/summary")
    assert resp.status_code == 200
    summary = resp.json()
    assert len(summary) == 1
    assert summary[0]["count_in_stock"] == 5


def test_create_roll_bulk_quantity_with_serial_rejected(product_manager_client, brand, ftype):
    resp = product_manager_client.post(
        "/api/v1/filament/rolls",
        json={
            "brand_id": brand.id, "type_id": ftype.id, "weight_grams": 1000, "color": "Schwarz",
            "vendor_serial": "SN123", "quantity": 2,
        },
    )
    assert resp.status_code == 400


def test_create_roll_duplicate_active_serial_conflicts(product_manager_client, brand, ftype):
    body = {"brand_id": brand.id, "type_id": ftype.id, "weight_grams": 1000, "color": "Schwarz", "vendor_serial": "SN123"}
    resp = product_manager_client.post("/api/v1/filament/rolls", json=body)
    assert resp.status_code == 201

    resp = product_manager_client.post("/api/v1/filament/rolls", json=body)
    assert resp.status_code == 409


def test_create_roll_unknown_brand_404(product_manager_client, ftype):
    resp = product_manager_client.post(
        "/api/v1/filament/rolls",
        json={"brand_id": 999, "type_id": ftype.id, "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 404


def test_list_rolls_default_active_only(product_manager_client, brand, ftype, db):
    r1 = FilamentRoll(brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz", added_by="pm")
    r2 = FilamentRoll(brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Rot", added_by="pm")
    db.add_all([r1, r2])
    db.commit()
    r2.removed_at = r2.added_at
    r2.removed_by = "pm"
    db.commit()

    resp = product_manager_client.get("/api/v1/filament/rolls")
    assert resp.status_code == 200
    colors = [r["color"] for r in resp.json()]
    assert colors == ["Schwarz"]

    resp = product_manager_client.get("/api/v1/filament/rolls?status=all")
    assert len(resp.json()) == 2


def test_remove_and_restore_roll(product_manager_client, brand, ftype, db):
    roll = FilamentRoll(brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz", added_by="pm")
    db.add(roll)
    db.commit()

    resp = product_manager_client.post(f"/api/v1/filament/rolls/{roll.id}/remove")
    assert resp.status_code == 200
    assert resp.json()["in_stock"] is False

    resp = product_manager_client.post(f"/api/v1/filament/rolls/{roll.id}/remove")
    assert resp.status_code == 409

    resp = product_manager_client.post(f"/api/v1/filament/rolls/{roll.id}/restore")
    assert resp.status_code == 200
    assert resp.json()["in_stock"] is True

    resp = product_manager_client.post(f"/api/v1/filament/rolls/{roll.id}/restore")
    assert resp.status_code == 400


def test_update_roll(product_manager_client, brand, ftype, db):
    roll = FilamentRoll(brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz", added_by="pm")
    db.add(roll)
    db.commit()

    resp = product_manager_client.put(f"/api/v1/filament/rolls/{roll.id}", json={"color": "Weiss"})
    assert resp.status_code == 200
    assert resp.json()["color"] == "Weiss"


# ---------------------------------------------------------------------------
# Device: /scan — serialized tags (deterministic check-in/check-out)
# ---------------------------------------------------------------------------

def test_scan_unknown_serial_checks_in_and_auto_creates_brand_type(client, machine_token):
    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/scan",
        headers=_auth_header(token),
        json={
            "brand_name": "Bambu Lab", "type_name": "PLA Basic", "weight_grams": 1000,
            "color": "Schwarz", "vendor_serial": "BBL-0001",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["identify_required"] is False
    assert data["action"] == "checked_in"
    assert data["roll"]["brand_name"] == "Bambu Lab"
    assert data["roll"]["type_name"] == "PLA Basic"
    assert data["roll"]["in_stock"] is True


def test_scan_known_serial_checks_out(client, machine_token):
    token, _machine = machine_token
    body = {
        "brand_name": "Bambu Lab", "type_name": "PLA Basic", "weight_grams": 1000,
        "color": "Schwarz", "vendor_serial": "BBL-0001",
    }
    resp = client.post("/api/v1/filament/scan", headers=_auth_header(token), json=body)
    assert resp.json()["action"] == "checked_in"

    # Scanning the same serial again (e.g. the tag on the other side of the
    # spool, but with the same manufacturer serial) checks it back out.
    resp = client.post("/api/v1/filament/scan", headers=_auth_header(token), json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "checked_out"
    assert data["roll"]["in_stock"] is False

    # A third scan with the same serial checks a *new* physical instance in
    # again — it's no longer active, so it's treated as unknown.
    resp = client.post("/api/v1/filament/scan", headers=_auth_header(token), json=body)
    assert resp.json()["action"] == "checked_in"


def test_scan_reuses_existing_brand_and_type_by_name(client, machine_token, brand, ftype, db):
    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/scan",
        headers=_auth_header(token),
        json={
            "brand_name": "Creality", "type_name": "Hyper PLA RFID", "weight_grams": 1000,
            "color": "Schwarz", "vendor_serial": "CR-0001",
        },
    )
    data = resp.json()["roll"]
    assert data["brand_id"] == brand.id
    assert data["type_id"] == ftype.id
    assert db.query(FilamentBrand).count() == 1
    assert db.query(FilamentType).count() == 1


def test_scan_without_serial_does_not_act_and_reports_stock(client, machine_token, brand, ftype, db):
    token, _machine = machine_token
    db.add(FilamentRoll(brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz", added_by="pm"))
    db.commit()

    resp = client.post(
        "/api/v1/filament/scan",
        headers=_auth_header(token),
        json={"brand_name": "Creality", "type_name": "Hyper PLA RFID", "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["identify_required"] is True
    assert data["action"] is None
    assert data["current_stock"] == 1


def test_scan_without_serial_unknown_spec_reports_zero_stock(client, machine_token):
    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/scan",
        headers=_auth_header(token),
        json={"brand_name": "Generic", "type_name": "PLA", "weight_grams": 1000, "color": "Grau"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["identify_required"] is True
    assert data["current_stock"] == 0


# ---------------------------------------------------------------------------
# Device: /status — read-only lookup, never checks anything in or out
# ---------------------------------------------------------------------------

def test_status_unknown_serial_reports_not_in_stock(client, machine_token):
    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/status", headers=_auth_header(token), json={"vendor_serial": "BBL-9999"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["in_stock"] is False
    assert data["roll"] is None


def test_status_in_stock_serial_reports_roll_without_acting(client, machine_token, db):
    token, _machine = machine_token
    scan_body = {
        "brand_name": "Bambu Lab", "type_name": "PETG", "weight_grams": 1000,
        "color": "Schwarz", "vendor_serial": "BBL-0002",
    }
    client.post("/api/v1/filament/scan", headers=_auth_header(token), json=scan_body)

    resp = client.post(
        "/api/v1/filament/status", headers=_auth_header(token), json={"vendor_serial": "BBL-0002"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["in_stock"] is True
    assert data["roll"]["type_name"] == "PETG"

    # A repeated /status call must not have changed anything — still in stock.
    resp = client.post(
        "/api/v1/filament/status", headers=_auth_header(token), json={"vendor_serial": "BBL-0002"}
    )
    assert resp.json()["in_stock"] is True
    assert db.query(FilamentRoll).filter(FilamentRoll.vendor_serial == "BBL-0002").count() == 1


def test_status_reports_not_in_stock_after_checkout(client, machine_token):
    token, _machine = machine_token
    scan_body = {
        "brand_name": "Bambu Lab", "type_name": "PLA Basic", "weight_grams": 1000,
        "color": "Weiss", "vendor_serial": "BBL-0003",
    }
    client.post("/api/v1/filament/scan", headers=_auth_header(token), json=scan_body)
    client.post("/api/v1/filament/scan", headers=_auth_header(token), json=scan_body)  # checks it back out

    resp = client.post(
        "/api/v1/filament/status", headers=_auth_header(token), json={"vendor_serial": "BBL-0003"}
    )
    assert resp.json() == {"in_stock": False, "roll": None}


# ---------------------------------------------------------------------------
# Device: /checkin, /checkout — OpenSpool-style manual flow (no serial)
# ---------------------------------------------------------------------------

def test_device_checkin_without_serial_auto_creates_and_adds(client, machine_token):
    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/checkin",
        headers=_auth_header(token),
        json={"brand_name": "OpenSpool Generic", "type_name": "PLA", "weight_grams": 1000, "color": "Blau"},
    )
    assert resp.status_code == 200
    assert resp.json()["vendor_serial"] is None
    assert resp.json()["in_stock"] is True


def test_device_checkout_without_serial_picks_oldest_matching_fifo(client, machine_token, brand, ftype, db):
    from datetime import UTC, datetime, timedelta

    older = FilamentRoll(
        brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz", added_by="pm",
        added_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2),
    )
    newer = FilamentRoll(
        brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz", added_by="pm",
        added_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
    )
    db.add_all([older, newer])
    db.commit()

    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/checkout",
        headers=_auth_header(token),
        json={"brand_name": "Creality", "type_name": "Hyper PLA RFID", "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == older.id
    assert resp.json()["in_stock"] is False


def test_device_checkout_without_serial_never_matches_serialized_rolls(client, machine_token, brand, ftype, db):
    db.add(FilamentRoll(
        brand_id=brand.id, type_id=ftype.id, weight_grams=1000, color="Schwarz",
        vendor_serial="CR-9999", added_by="pm",
    ))
    db.commit()

    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/checkout",
        headers=_auth_header(token),
        json={"brand_name": "Creality", "type_name": "Hyper PLA RFID", "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 404


def test_device_checkout_no_stock_404(client, machine_token, brand, ftype):
    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/checkout",
        headers=_auth_header(token),
        json={"brand_name": "Creality", "type_name": "Hyper PLA RFID", "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 404


def test_device_checkout_unknown_spec_404(client, machine_token):
    token, _machine = machine_token
    resp = client.post(
        "/api/v1/filament/checkout",
        headers=_auth_header(token),
        json={"brand_name": "Nonexistent", "type_name": "Nonexistent", "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 404


def test_device_endpoints_require_token(client):
    resp = client.post(
        "/api/v1/filament/scan",
        json={"brand_name": "Creality", "type_name": "PLA", "weight_grams": 1000, "color": "Schwarz"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Web page
# ---------------------------------------------------------------------------

def test_filament_page_is_public(client):
    """Public like /products — see CLAUDE.md #75. An anonymous visitor gets
    canManage=false and loggedIn=false, so the template never even attempts
    to fetch the session-only brands/types/rolls endpoints."""
    resp = client.get("/filament")
    assert resp.status_code == 200
    assert "filamentManage(false, false)" in resp.text


def test_filament_page_renders_readonly_for_plain_treasurer(treasurer_client):
    """Logged in but not a product manager: canManage=false, loggedIn=true —
    see CLAUDE.md #74."""
    resp = treasurer_client.get("/filament")
    assert resp.status_code == 200
    assert "filamentManage(false, true)" in resp.text


def test_filament_page_renders_for_product_manager(product_manager_client):
    resp = product_manager_client.get("/filament")
    assert resp.status_code == 200
    assert "filamentManage(true, true)" in resp.text


def test_filament_page_renders_for_admin(admin_client):
    resp = admin_client.get("/filament")
    assert resp.status_code == 200
    assert "filamentManage(true, true)" in resp.text


# ---------------------------------------------------------------------------
# Color aliases
# ---------------------------------------------------------------------------

def _add_roll(client, brand, ftype, color):
    resp = client.post(
        "/api/v1/filament/rolls",
        json={"brand_id": brand.id, "type_id": ftype.id, "weight_grams": 1000, "color": color},
    )
    assert resp.status_code == 201
    return resp.json()[0]


def _add_alias(client, **body):
    return client.post("/api/v1/filament/color-aliases", json=body)


def test_color_aliases_readable_by_any_session_user(treasurer_client):
    assert treasurer_client.get("/api/v1/filament/color-aliases").status_code == 200


def test_create_color_alias_requires_product_manager(treasurer_client):
    resp = _add_alias(treasurer_client, hex="#000000", name="Schwarz")
    assert resp.status_code == 403


def test_create_color_alias_normalizes_hex_and_lists(product_manager_client):
    resp = _add_alias(product_manager_client, hex="#ff00aa", name=" Pink ")
    assert resp.status_code == 201
    body = resp.json()
    assert body["hex"] == "#FF00AA"
    assert body["name"] == "Pink"
    assert body["brand_id"] is None and body["type_id"] is None
    listed = product_manager_client.get("/api/v1/filament/color-aliases").json()
    assert [a["hex"] for a in listed] == ["#FF00AA"]


def test_create_color_alias_rejects_non_hex(product_manager_client):
    assert _add_alias(product_manager_client, hex="schwarz", name="Schwarz").status_code == 422
    assert _add_alias(product_manager_client, hex="#12345", name="Kurz").status_code == 422


def test_create_color_alias_duplicate_scope_conflicts(product_manager_client, brand):
    assert _add_alias(product_manager_client, hex="#000000", name="Schwarz").status_code == 201
    assert _add_alias(product_manager_client, hex="#000000", name="Black").status_code == 409
    # Same hex but a different scope is a distinct alias.
    assert _add_alias(product_manager_client, hex="#000000", name="Tiefschwarz", brand_id=brand.id).status_code == 201
    assert _add_alias(product_manager_client, hex="#000000", name="Noch eins", brand_id=brand.id).status_code == 409


def test_create_color_alias_unknown_brand_or_type_404(product_manager_client):
    assert _add_alias(product_manager_client, hex="#000000", name="X", brand_id=999).status_code == 404
    assert _add_alias(product_manager_client, hex="#000000", name="X", type_id=999).status_code == 404


def test_delete_color_alias(product_manager_client):
    alias_id = _add_alias(product_manager_client, hex="#000000", name="Schwarz").json()["id"]
    assert product_manager_client.delete(f"/api/v1/filament/color-aliases/{alias_id}").status_code == 200
    assert product_manager_client.get("/api/v1/filament/color-aliases").json() == []
    assert product_manager_client.delete(f"/api/v1/filament/color-aliases/{alias_id}").status_code == 404


def test_roll_and_summary_show_alias_but_keep_raw_color(product_manager_client, client, brand, ftype):
    _add_alias(product_manager_client, hex="#000000", name="Schwarz")
    roll = _add_roll(product_manager_client, brand, ftype, "#000000")
    assert roll["color"] == "#000000"
    assert roll["color_name"] == "Schwarz"

    listed = product_manager_client.get("/api/v1/filament/rolls").json()
    assert listed[0]["color_name"] == "Schwarz"

    # The public summary resolves it too (anonymous client).
    summary = client.get("/api/v1/filament/rolls/summary").json()
    assert summary[0]["color"] == "#000000"
    assert summary[0]["color_name"] == "Schwarz"


def test_alias_match_is_case_insensitive_and_ignores_non_hex(product_manager_client, brand, ftype):
    _add_alias(product_manager_client, hex="#00FF00", name="Grün")
    assert _add_roll(product_manager_client, brand, ftype, "#00ff00")["color_name"] == "Grün"
    assert _add_roll(product_manager_client, brand, ftype, "Grün")["color_name"] is None


def test_alias_without_match_has_no_color_name(product_manager_client, brand, ftype):
    assert _add_roll(product_manager_client, brand, ftype, "#123456")["color_name"] is None


def test_most_specific_alias_wins(product_manager_client, db, brand, ftype):
    other_type = FilamentType(name="PETG")
    db.add(other_type)
    db.commit()
    _add_alias(product_manager_client, hex="#000000", name="Schwarz")
    _add_alias(product_manager_client, hex="#000000", name="Marken-Schwarz", brand_id=brand.id)
    _add_alias(product_manager_client, hex="#000000", name="Hyper-Schwarz", type_id=ftype.id)

    # type-scoped beats brand-scoped beats unscoped
    assert _add_roll(product_manager_client, brand, ftype, "#000000")["color_name"] == "Hyper-Schwarz"
    # other type of the same brand: brand-scoped alias applies
    assert _add_roll(product_manager_client, brand, other_type, "#000000")["color_name"] == "Marken-Schwarz"


def test_alias_scope_does_not_leak_to_other_brand(product_manager_client, db, brand, ftype):
    other_brand = FilamentBrand(name="Bambu Lab")
    db.add(other_brand)
    db.commit()
    _add_alias(product_manager_client, hex="#000000", name="Marken-Schwarz", brand_id=brand.id)
    assert _add_roll(product_manager_client, other_brand, ftype, "#000000")["color_name"] is None


def test_deleting_brand_removes_its_scoped_aliases(product_manager_client, brand):
    _add_alias(product_manager_client, hex="#000000", name="Marken-Schwarz", brand_id=brand.id)
    assert product_manager_client.delete(f"/api/v1/filament/brands/{brand.id}").status_code == 200
    assert product_manager_client.get("/api/v1/filament/color-aliases").json() == []


def test_deleting_type_removes_its_scoped_aliases(product_manager_client, ftype):
    _add_alias(product_manager_client, hex="#000000", name="Hyper-Schwarz", type_id=ftype.id)
    assert product_manager_client.delete(f"/api/v1/filament/types/{ftype.id}").status_code == 200
    assert product_manager_client.get("/api/v1/filament/color-aliases").json() == []
