from datetime import date
from decimal import Decimal

import pytest

from app.models.ledger import BankAccount, LedgerCategory, LedgerCategoryKind, LedgerSphere

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bank_account(db):
    a = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("0.00"))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def ideell_income_category(db):
    c = LedgerCategory(
        name="Mitgliedsbeiträge", slug="mitgliedsbeitraege",
        kind=LedgerCategoryKind.income, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def wgb_income_category(db):
    c = LedgerCategory(
        name="Sponsoring", slug="sponsoring",
        kind=LedgerCategoryKind.income, sphere=LedgerSphere.wirtschaftlicher_geschaeftsbetrieb, active=True,
    )
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def expense_category(db):
    c = LedgerCategory(
        name="Wareneinkauf", slug="wareneinkauf",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.zweckbetrieb, active=True,
    )
    db.add(c)
    db.commit()
    return c


def _book(client, bank_account, category, amount, entry_date):
    resp = client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": entry_date,
            "description": "Buchung",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": str(amount)},
                {"category_id": category.id, "amount": str(-Decimal(amount))},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# GET/POST/PUT/DELETE /ledger/reserves
# ---------------------------------------------------------------------------

def test_list_reserves_requires_auth(client):
    resp = client.get("/api/v1/ledger/reserves")
    assert resp.status_code == 401


def test_auditor_cannot_create_reserve(auditor_client):
    resp = auditor_client.post("/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"})
    assert resp.status_code in (401, 403)


def test_create_reserve_frei_minimal(treasurer_client):
    resp = treasurer_client.post("/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "frei"
    assert body["purpose"] is None
    assert body["balance"] == "0.00"


def test_create_reserve_zweckgebunden_requires_purpose_and_target_date(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/reserves",
        json={"name": "Neue Werkstatt", "kind": "zweckgebunden"},
    )
    assert resp.status_code == 400


def test_create_reserve_zweckgebunden_success(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/reserves",
        json={
            "name": "Neue Werkstatt", "kind": "zweckgebunden",
            "purpose": "Anbau Werkstattgebäude", "target_date": "2028-01-01",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["target_date"] == "2028-01-01"


def test_create_reserve_wiederbeschaffung_with_sphere(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/reserves",
        json={"name": "Wiederbeschaffung Lasercutter", "kind": "wiederbeschaffung", "sphere": "zweckbetrieb"},
    )
    assert resp.status_code == 201
    assert resp.json()["sphere"] == "zweckbetrieb"


def test_update_reserve_fields(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    resp = treasurer_client.put(
        f"/api/v1/ledger/reserves/{reserve_id}", json={"name": "Freie Rücklage (allgemein)"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Freie Rücklage (allgemein)"


def test_update_reserve_to_zweckgebunden_without_purpose_rejected(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Rücklage", "kind": "frei"},
    ).json()["id"]
    resp = treasurer_client.put(f"/api/v1/ledger/reserves/{reserve_id}", json={"kind": "zweckgebunden"})
    assert resp.status_code == 400


def test_update_reserve_clear_target_date(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves",
        json={"name": "Neue Werkstatt", "kind": "zweckgebunden", "purpose": "Anbau", "target_date": "2028-01-01"},
    ).json()["id"]
    # Switching away from zweckgebunden first, since clearing target_date
    # while still zweckgebunden would itself violate the requirement.
    resp = treasurer_client.put(
        f"/api/v1/ledger/reserves/{reserve_id}", json={"kind": "frei", "clear_target_date": True},
    )
    assert resp.status_code == 200
    assert resp.json()["target_date"] is None


def test_update_unknown_reserve_404(treasurer_client):
    resp = treasurer_client.put("/api/v1/ledger/reserves/9999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_reserve_without_movements(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    resp = treasurer_client.delete(f"/api/v1/ledger/reserves/{reserve_id}")
    assert resp.status_code == 204
    assert treasurer_client.get("/api/v1/ledger/reserves").json() == []


def test_delete_reserve_with_movements_rejected(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-01-01", "amount": "100.00"},
    )
    resp = treasurer_client.delete(f"/api/v1/ledger/reserves/{reserve_id}")
    assert resp.status_code == 409


def test_delete_unknown_reserve_404(treasurer_client):
    resp = treasurer_client.delete("/api/v1/ledger/reserves/9999")
    assert resp.status_code == 404


def test_auditor_cannot_delete_reserve(auditor_client, treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    resp = auditor_client.delete(f"/api/v1/ledger/reserves/{reserve_id}")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET/POST/PUT/DELETE /ledger/reserve-movements
# ---------------------------------------------------------------------------

def test_auditor_cannot_create_movement(auditor_client, treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    resp = auditor_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-01-01", "amount": "100.00"},
    )
    assert resp.status_code in (401, 403)


def test_create_movement_unknown_reserve_404(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": 9999, "movement_date": "2026-01-01", "amount": "100.00"},
    )
    assert resp.status_code == 404


def test_movement_updates_reserve_balance(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-01-01", "amount": "500.00", "note": "Zuführung 2026"},
    )
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-06-01", "amount": "-200.00", "note": "Teilauflösung"},
    )
    [reserve] = treasurer_client.get("/api/v1/ledger/reserves").json()
    assert reserve["balance"] == "300.00"


def test_reserve_balance_as_of_excludes_later_movements(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2025-01-01", "amount": "500.00"},
    )
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-01-01", "amount": "300.00"},
    )
    [reserve] = treasurer_client.get("/api/v1/ledger/reserves", params={"as_of": "2025-12-31"}).json()
    assert reserve["balance"] == "500.00"


def test_list_movements_filtered_by_reserve(treasurer_client):
    r1 = treasurer_client.post("/api/v1/ledger/reserves", json={"name": "A", "kind": "frei"}).json()["id"]
    r2 = treasurer_client.post("/api/v1/ledger/reserves", json={"name": "B", "kind": "frei"}).json()["id"]
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": r1, "movement_date": "2026-01-01", "amount": "100.00"},
    )
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": r2, "movement_date": "2026-01-01", "amount": "50.00"},
    )
    resp = treasurer_client.get("/api/v1/ledger/reserve-movements", params={"reserve_id": r1})
    [m] = resp.json()
    assert m["reserve_id"] == r1


def test_update_movement(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    movement_id = treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-01-01", "amount": "100.00"},
    ).json()["id"]
    resp = treasurer_client.put(
        f"/api/v1/ledger/reserve-movements/{movement_id}", json={"amount": "150.00", "note": "korrigiert"},
    )
    assert resp.status_code == 200
    assert resp.json()["amount"] == "150.00"
    assert resp.json()["note"] == "korrigiert"


def test_update_unknown_movement_404(treasurer_client):
    resp = treasurer_client.put("/api/v1/ledger/reserve-movements/9999", json={"amount": "1.00"})
    assert resp.status_code == 404


def test_delete_movement(treasurer_client):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    movement_id = treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-01-01", "amount": "100.00"},
    ).json()["id"]
    resp = treasurer_client.delete(f"/api/v1/ledger/reserve-movements/{movement_id}")
    assert resp.status_code == 204
    assert treasurer_client.get("/api/v1/ledger/reserve-movements").json() == []


def test_delete_unknown_movement_404(treasurer_client):
    resp = treasurer_client.delete("/api/v1/ledger/reserve-movements/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /ledger/report/mittelverwendung
# ---------------------------------------------------------------------------

def test_mittelverwendung_requires_auth(client):
    resp = client.get("/api/v1/ledger/report/mittelverwendung", params={"year": 2026})
    assert resp.status_code == 401


def test_mittelverwendung_excludes_wgb_sphere(
    treasurer_client, bank_account, ideell_income_category, wgb_income_category,
):
    _book(treasurer_client, bank_account, ideell_income_category, "1000.00", "2026-03-01")
    _book(treasurer_client, bank_account, wgb_income_category, "500.00", "2026-03-01")

    report = treasurer_client.get("/api/v1/ledger/report/mittelverwendung", params={"year": 2026}).json()
    assert report["cumulative_relevant_net_result"] == "1000.00"
    assert report["cumulative_reserve_zufuehrungen"] == "0.00"
    assert report["available_funds"] == "1000.00"


def test_mittelverwendung_zufuehrung_reduces_available_funds(
    treasurer_client, bank_account, ideell_income_category,
):
    _book(treasurer_client, bank_account, ideell_income_category, "1000.00", "2026-03-01")
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-06-01", "amount": "400.00"},
    )

    report = treasurer_client.get("/api/v1/ledger/report/mittelverwendung", params={"year": 2026}).json()
    assert report["cumulative_reserve_zufuehrungen"] == "400.00"
    assert report["available_funds"] == "600.00"
    [reserve] = report["reserves"]
    assert reserve["balance"] == "400.00"


def test_mittelverwendung_aufloesung_increases_available_funds(
    treasurer_client, bank_account, ideell_income_category,
):
    _book(treasurer_client, bank_account, ideell_income_category, "1000.00", "2026-01-01")
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-02-01", "amount": "400.00"},
    )
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-08-01", "amount": "-150.00"},
    )

    report = treasurer_client.get("/api/v1/ledger/report/mittelverwendung", params={"year": 2026}).json()
    assert report["cumulative_reserve_zufuehrungen"] == "400.00"
    assert report["cumulative_reserve_aufloesungen"] == "150.00"
    assert report["available_funds"] == "750.00"  # 1000 - 400 + 150


def test_mittelverwendung_cumulative_across_years(
    treasurer_client, bank_account, ideell_income_category,
):
    _book(treasurer_client, bank_account, ideell_income_category, "1000.00", "2025-06-01")
    _book(treasurer_client, bank_account, ideell_income_category, "500.00", "2026-06-01")

    report_2025 = treasurer_client.get("/api/v1/ledger/report/mittelverwendung", params={"year": 2025}).json()
    report_2026 = treasurer_client.get("/api/v1/ledger/report/mittelverwendung", params={"year": 2026}).json()
    assert report_2025["cumulative_relevant_net_result"] == "1000.00"
    assert report_2026["cumulative_relevant_net_result"] == "1500.00"


def test_mittelverwendung_reserve_balance_as_of_year_end_ignores_future_movements(
    treasurer_client, bank_account, ideell_income_category,
):
    reserve_id = treasurer_client.post(
        "/api/v1/ledger/reserves", json={"name": "Freie Rücklage", "kind": "frei"},
    ).json()["id"]
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2026-06-01", "amount": "200.00"},
    )
    treasurer_client.post(
        "/api/v1/ledger/reserve-movements",
        json={"reserve_id": reserve_id, "movement_date": "2027-01-01", "amount": "300.00"},
    )
    report_2026 = treasurer_client.get("/api/v1/ledger/report/mittelverwendung", params={"year": 2026}).json()
    [reserve] = report_2026["reserves"]
    assert reserve["balance"] == "200.00"
