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
def purchase_category(db):
    """The category an asset's purchase is originally booked against — may
    differ from the AfA target category, see Key Design Decision #35."""
    c = LedgerCategory(
        name="Anschaffungen", slug="anschaffungen",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.zweckbetrieb, active=True,
    )
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def afa_category(db):
    c = LedgerCategory(
        name="Abschreibungen", slug="abschreibungen",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.zweckbetrieb, active=True,
    )
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def income_category(db):
    c = LedgerCategory(
        name="Mitgliedsbeiträge", slug="mitgliedsbeitraege",
        kind=LedgerCategoryKind.income, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(c)
    db.commit()
    return c


def _book_purchase(client, bank_account, category, amount, entry_date="2024-03-15"):
    """Books a normal two-line entry (bank -amount / category +amount) and
    returns the response JSON, same shape any manual purchase booking has —
    before it's ever linked as an asset."""
    resp = client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": entry_date,
            "description": "Lasercutter Speedy 400",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": f"-{amount}"},
                {"category_id": category.id, "amount": amount},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _category_line_id(entry_json):
    [line] = [l for l in entry_json["lines"] if l["category_id"] is not None]
    return line["id"]


def _category_line(entry_json):
    [line] = [l for l in entry_json["lines"] if l["category_id"] is not None]
    return line


def _full_component(entry_json):
    """A single component claiming a category line's full amount — the
    common case, equivalent to the old one-line-per-asset shape."""
    line = _category_line(entry_json)
    return {"entry_line_id": line["id"], "amount": line["amount"]}


# ---------------------------------------------------------------------------
# GET/POST/PUT/DELETE /ledger/assets
# ---------------------------------------------------------------------------

def test_list_assets_requires_auth(client):
    resp = client.get("/api/v1/ledger/assets")
    assert resp.status_code == 401


def test_auditor_cannot_create_asset(auditor_client, treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    resp = auditor_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "components": [_full_component(entry)],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code in (401, 403)


def test_create_asset_success(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter Speedy 400", "components": [_full_component(entry)],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["acquisition_cost"] == "1200.00"
    assert body["acquisition_date"] == "2024-03-15"  # defaulted from the entry
    assert body["useful_life_years"] == 5
    assert body["category_id"] == afa_category.id
    assert body["disposed_at"] is None
    [component] = body["components"]
    assert component["amount"] == "1200.00"
    assert component["entry_line_id"] == _category_line_id(entry)


def test_create_asset_explicit_acquisition_date_overrides_entry_date(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    component = _full_component(entry)
    component["acquisition_date"] = "2024-03-01"
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "components": [component],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["acquisition_date"] == "2024-03-01"
    assert resp.json()["components"][0]["acquisition_date"] == "2024-03-01"


def test_create_asset_rejects_bank_side_line(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    [bank_line] = [l for l in entry["lines"] if l["bank_account_id"] is not None]
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "components": [{"entry_line_id": bank_line["id"], "amount": "1200.00"}],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 400


def test_create_asset_rejects_income_category_line(treasurer_client, bank_account, income_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, income_category, "1200.00")
    # A "purchase" against an income category makes no real-world sense, but
    # exercise the guard directly regardless.
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "components": [_full_component(entry)],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 400


def test_create_asset_rejects_afa_category_that_is_income(treasurer_client, bank_account, purchase_category, income_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "components": [_full_component(entry)],
            "useful_life_years": 5, "category_id": income_category.id,
        },
    )
    assert resp.status_code == 400


def test_create_asset_rejects_already_fully_capitalized_line(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    component = _full_component(entry)
    first = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [component], "useful_life_years": 5, "category_id": afa_category.id},
    )
    assert first.status_code == 201
    second = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter (Dopplung)", "components": [component], "useful_life_years": 5, "category_id": afa_category.id},
    )
    assert second.status_code == 400
    assert "remaining capitalizable amount" in second.json()["detail"]


def test_create_asset_unknown_entry_line_404(treasurer_client, afa_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "components": [{"entry_line_id": 9999, "amount": "100.00"}],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 404


def test_create_asset_unknown_category_404(treasurer_client, bank_account, purchase_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": 9999},
    )
    assert resp.status_code == 404


def test_list_assets_includes_computed_book_value(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": afa_category.id},
    )
    # 10 depreciable months in 2024 (March..December) at 1200/60 = 20/month -> 200.00
    resp = treasurer_client.get("/api/v1/ledger/assets", params={"as_of": "2024-12-31"})
    assert resp.status_code == 200
    [asset] = resp.json()
    assert asset["accumulated_depreciation"] == "200.00"
    assert asset["book_value"] == "1000.00"


def test_update_asset_useful_life_and_category(treasurer_client, bank_account, purchase_category, afa_category, db):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    asset_id = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": afa_category.id},
    ).json()["id"]

    other_afa = LedgerCategory(name="Abschreibungen 2", slug="abschreibungen-2", kind=LedgerCategoryKind.expense, sphere=LedgerSphere.zweckbetrieb, active=True)
    db.add(other_afa)
    db.commit()

    resp = treasurer_client.put(
        f"/api/v1/ledger/assets/{asset_id}",
        json={"useful_life_years": 10, "category_id": other_afa.id, "name": "Lasercutter Speedy 400 Flexx"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["useful_life_years"] == 10
    assert body["category_id"] == other_afa.id
    assert body["name"] == "Lasercutter Speedy 400 Flexx"


def test_update_asset_afa_category_must_stay_expense(treasurer_client, bank_account, purchase_category, afa_category, income_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    asset_id = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": afa_category.id},
    ).json()["id"]
    resp = treasurer_client.put(f"/api/v1/ledger/assets/{asset_id}", json={"category_id": income_category.id})
    assert resp.status_code == 400


def test_update_asset_component_dispose_and_clear(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": afa_category.id},
    ).json()
    component_id = asset["components"][0]["id"]

    resp = treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{component_id}", json={"disposed_at": "2025-06-30"},
    )
    assert resp.status_code == 200
    assert resp.json()["disposed_at"] == "2025-06-30"
    assert resp.json()["components"][0]["disposed_at"] == "2025-06-30"

    resp = treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{component_id}", json={"clear_disposed_at": True},
    )
    assert resp.status_code == 200
    assert resp.json()["disposed_at"] is None


def test_update_asset_component_dispose_before_acquisition_rejected(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": afa_category.id},
    ).json()
    component_id = asset["components"][0]["id"]
    resp = treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{component_id}", json={"disposed_at": "2024-01-01"},
    )
    assert resp.status_code == 400


def test_dispose_one_component_leaves_others_in_service(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    """The user's own motivating example: a graphics card sold/scrapped
    while the rest of a multi-part PC asset stays in service (#50)."""
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00", entry_date="2024-03-01")
    gpu = _book_purchase(treasurer_client, bank_account, purchase_category, "600.00", entry_date="2024-03-01")
    asset = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "PC", "components": [_full_component(case), _full_component(gpu)],
            "useful_life_years": 3, "category_id": afa_category.id,
        },
    ).json()
    gpu_component_id = next(c["id"] for c in asset["components"] if c["entry_line_id"] == _category_line_id(gpu))

    resp = treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{gpu_component_id}", json={"disposed_at": "2024-06-30"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Whole-asset disposed_at stays None — only one part is gone, not all of it.
    assert body["disposed_at"] is None
    disposed = next(c for c in body["components"] if c["id"] == gpu_component_id)
    still_active = next(c for c in body["components"] if c["id"] != gpu_component_id)
    assert disposed["disposed_at"] == "2024-06-30"
    assert still_active["disposed_at"] is None


def test_update_unknown_asset_404(treasurer_client):
    resp = treasurer_client.put("/api/v1/ledger/assets/9999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_asset_reverts_to_normal_expense(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset_id = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": afa_category.id},
    ).json()["id"]

    resp = treasurer_client.delete(f"/api/v1/ledger/assets/{asset_id}")
    assert resp.status_code == 204
    assert treasurer_client.get("/api/v1/ledger/assets").json() == []

    report = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2024}).json()
    [cat] = [c for c in report["categories"] if c["category_id"] == purchase_category.id]
    assert cat["total"] == "1200.00"


def test_delete_unknown_asset_404(treasurer_client):
    resp = treasurer_client.delete("/api/v1/ledger/assets/9999")
    assert resp.status_code == 404


def test_auditor_cannot_delete_asset(auditor_client, treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    asset_id = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "components": [_full_component(entry)], "useful_life_years": 5, "category_id": afa_category.id},
    ).json()["id"]
    resp = auditor_client.delete(f"/api/v1/ledger/assets/{asset_id}")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# EÜR integration: capitalized purchases are excluded, AfA is folded in instead
# ---------------------------------------------------------------------------

def _capitalize(treasurer_client, entry, afa_category, useful_life_years=5, name="Lasercutter"):
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": name, "components": [_full_component(entry)],
            "useful_life_years": useful_life_years, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_euer_excludes_full_purchase_amount_in_purchase_year(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")

    before = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2024}).json()
    assert before["total_expense"] == "1200.00"

    _capitalize(treasurer_client, entry, afa_category)

    after = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2024}).json()
    # Full purchase no longer counts — only the acquisition-year AfA (10
    # months at 1200/60 = 20/month) shows up instead, against afa_category.
    assert after["total_expense"] == "200.00"
    assert [c["category_id"] for c in after["categories"]] == [afa_category.id]
    assert after["categories"][0]["total"] == "200.00"


def test_euer_afa_full_years_and_final_partial_year_sum_to_acquisition_cost(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    _capitalize(treasurer_client, entry, afa_category, useful_life_years=5)

    def afa_for(year):
        report = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": year}).json()
        if not report["categories"]:
            return Decimal("0.00")
        return Decimal(report["categories"][0]["total"])

    assert afa_for(2023) == Decimal("0.00")  # before acquisition
    assert afa_for(2024) == Decimal("200.00")  # 10 months, monatsgenau
    assert afa_for(2025) == Decimal("240.00")  # full year
    assert afa_for(2026) == Decimal("240.00")
    assert afa_for(2027) == Decimal("240.00")
    assert afa_for(2028) == Decimal("240.00")
    assert afa_for(2029) == Decimal("40.00")  # final 2 months
    assert afa_for(2030) == Decimal("0.00")  # useful life exhausted

    total = sum((afa_for(y) for y in range(2023, 2031)), Decimal("0.00"))
    assert total == Decimal("1200.00")  # no rounding drift across the whole schedule


def test_euer_afa_stops_after_disposal(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset = _capitalize(treasurer_client, entry, afa_category, useful_life_years=5)
    component_id = asset["components"][0]["id"]
    treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{component_id}", json={"disposed_at": "2025-06-30"},
    )

    def afa_for(year):
        report = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": year}).json()
        if not report["categories"]:
            return Decimal("0.00")
        return Decimal(report["categories"][0]["total"])

    # 2025: only Jan-Jun (6 more months after 2024's 10) = 6*20 = 120.00
    assert afa_for(2025) == Decimal("120.00")
    assert afa_for(2026) == Decimal("0.00")


def test_euer_afa_combines_with_other_bookings_in_same_category(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    _capitalize(treasurer_client, entry, afa_category)

    # A normal, non-capitalized booking landing in the same AfA category
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2024-06-01",
            "description": "Sonstige Abschreibung",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "-50.00"},
                {"category_id": afa_category.id, "amount": "50.00"},
            ],
        },
    )
    report = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2024}).json()
    [cat] = report["categories"]
    assert cat["total"] == "250.00"  # 200.00 AfA + 50.00 manual booking


# ---------------------------------------------------------------------------
# Key Design Decision #49: partial-line capitalization and multi-component
# assets (e.g. several individually-bought parts of one computer)
# ---------------------------------------------------------------------------

def test_create_asset_partial_amount_leaves_remainder_as_normal_expense(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1000.00", entry_date="2024-03-15")
    line = _category_line(entry)
    # Only 900 of the 1000 booked is actually the capitalizable asset; the
    # rest (e.g. accessories/consumables on the same invoice) stays a normal
    # one-off expense in the purchase category.
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Werkbank", "components": [{"entry_line_id": line["id"], "amount": "900.00"}],
            "useful_life_years": 9, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["acquisition_cost"] == "900.00"

    report = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2024}).json()
    by_category = {c["category_id"]: Decimal(c["total"]) for c in report["categories"]}
    # 10 depreciable months at 900/(9*12)=8.3333.../month -> 83.33
    assert by_category[afa_category.id] == Decimal("83.33")
    # Remainder (1000 - 900 = 100) still counts normally in the purchase category
    assert by_category[purchase_category.id] == Decimal("100.00")


def test_create_asset_from_multiple_components_combines_into_one_wirtschaftsgut(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    # A computer's individually-bought parts: none exceeds the GWG threshold
    # alone, but combined they form one Wirtschaftsgut and must be
    # capitalized together (funktionaler Zusammenhang).
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00", entry_date="2024-03-01")
    board = _book_purchase(treasurer_client, bank_account, purchase_category, "350.00", entry_date="2024-03-05")
    monitor = _book_purchase(treasurer_client, bank_account, purchase_category, "250.00", entry_date="2024-03-10")

    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "PC-Arbeitsplatz",
            "components": [_full_component(case), _full_component(board), _full_component(monitor)],
            "useful_life_years": 3, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["acquisition_cost"] == "900.00"
    assert body["acquisition_date"] == "2024-03-01"  # earliest of the three components
    assert len(body["components"]) == 3
    assert {c["entry_line_id"] for c in body["components"]} == {
        _category_line_id(case), _category_line_id(board), _category_line_id(monitor),
    }
    # Each component defaulted to its own entry's date (#50) — not one shared date.
    dates_by_line = {c["entry_line_id"]: c["acquisition_date"] for c in body["components"]}
    assert dates_by_line[_category_line_id(case)] == "2024-03-01"
    assert dates_by_line[_category_line_id(board)] == "2024-03-05"
    assert dates_by_line[_category_line_id(monitor)] == "2024-03-10"

    report = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2024}).json()
    assert [c["category_id"] for c in report["categories"]] == [afa_category.id]
    # 10 depreciable months each at 300/36, 350/36, 250/36 respectively,
    # rounded independently per component (Key Design Decision #50) and
    # summed — 83.33 + 97.22 + 69.44 = 249.99, a cent off the old single-
    # combined-calculation figure (250.00) since each component's own
    # remaining basis is now tracked (and rounded) separately, which is
    # what correctly allows per-component disposal later.
    assert report["categories"][0]["total"] == "249.99"


def test_add_asset_component_to_existing_asset(treasurer_client, bank_account, purchase_category, afa_category):
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00", entry_date="2024-03-01")
    asset = _capitalize(treasurer_client, case, afa_category, useful_life_years=3, name="PC")

    # A graphics card bought a few weeks later, added to the same asset —
    # the user's own motivating example (nachträgliche Anschaffungskosten).
    gpu = _book_purchase(treasurer_client, bank_account, purchase_category, "600.00", entry_date="2024-03-20")
    resp = treasurer_client.post(
        f"/api/v1/ledger/assets/{asset['id']}/components",
        json=_full_component(gpu),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["acquisition_cost"] == "900.00"
    assert len(body["components"]) == 2
    # Each component keeps its own acquisition_date — the GPU depreciates
    # from when it was actually bought, not backdated to the case's date.
    gpu_component = next(c for c in body["components"] if c["entry_line_id"] == _category_line_id(gpu))
    assert gpu_component["acquisition_date"] == "2024-03-20"


def test_add_asset_component_later_does_not_backdate_existing_afa(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    """The exact bug the user found: adding a component well after the
    asset's original purchase must not retroactively inflate past AfA."""
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00", entry_date="2024-01-01")
    asset = _capitalize(treasurer_client, case, afa_category, useful_life_years=3, name="PC")

    def afa_2024():
        report = treasurer_client.get("/api/v1/ledger/report/euer", params={"year": 2024}).json()
        return Decimal(report["categories"][0]["total"])

    # 12 months at 300/36 = 8.3333.../month -> 100.00 for the full year.
    assert afa_2024() == Decimal("100.00")

    # A year later (2025), a genuine upgrade is added to the same asset.
    upgrade = _book_purchase(treasurer_client, bank_account, purchase_category, "600.00", entry_date="2025-06-01")
    treasurer_client.post(f"/api/v1/ledger/assets/{asset['id']}/components", json=_full_component(upgrade))

    # 2024's already-reported AfA must be completely unchanged by the 2025 addition.
    assert afa_2024() == Decimal("100.00")


def test_add_asset_component_rejects_exceeding_remaining_amount(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1000.00")
    line = _category_line(entry)
    asset = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Werkbank", "components": [{"entry_line_id": line["id"], "amount": "600.00"}],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    ).json()

    # Only 400.00 remains uncapitalized on this line.
    resp = treasurer_client.post(
        f"/api/v1/ledger/assets/{asset['id']}/components",
        json={"entry_line_id": line["id"], "amount": "500.00"},
    )
    assert resp.status_code == 400
    assert "remaining capitalizable amount" in resp.json()["detail"]


def test_add_asset_component_unknown_asset_404(treasurer_client, bank_account, purchase_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00")
    resp = treasurer_client.post("/api/v1/ledger/assets/9999/components", json=_full_component(entry))
    assert resp.status_code == 404


def test_auditor_cannot_add_asset_component(
    auditor_client, treasurer_client, bank_account, purchase_category, afa_category,
):
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00")
    asset = _capitalize(treasurer_client, case, afa_category, name="PC")
    gpu = _book_purchase(treasurer_client, bank_account, purchase_category, "600.00")
    resp = auditor_client.post(f"/api/v1/ledger/assets/{asset['id']}/components", json=_full_component(gpu))
    assert resp.status_code in (401, 403)


def test_update_asset_component_amount(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1000.00")
    line = _category_line(entry)
    asset = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Werkbank", "components": [{"entry_line_id": line["id"], "amount": "600.00"}],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    ).json()
    [component] = asset["components"]

    resp = treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{component['id']}",
        json={"entry_line_id": line["id"], "amount": "800.00"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["acquisition_cost"] == "800.00"


def test_update_asset_component_rejects_exceeding_remaining_amount(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1000.00")
    line = _category_line(entry)
    asset = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Werkbank", "components": [{"entry_line_id": line["id"], "amount": "600.00"}],
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    ).json()
    [component] = asset["components"]

    resp = treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{component['id']}",
        json={"entry_line_id": line["id"], "amount": "1500.00"},
    )
    assert resp.status_code == 400


def test_delete_asset_component(treasurer_client, bank_account, purchase_category, afa_category):
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00", entry_date="2024-03-01")
    asset = _capitalize(treasurer_client, case, afa_category, useful_life_years=3, name="PC")
    gpu = _book_purchase(treasurer_client, bank_account, purchase_category, "600.00", entry_date="2024-03-20")
    added = treasurer_client.post(
        f"/api/v1/ledger/assets/{asset['id']}/components", json=_full_component(gpu),
    ).json()
    [gpu_component] = [c for c in added["components"] if c["entry_line_id"] == _category_line_id(gpu)]

    resp = treasurer_client.delete(f"/api/v1/ledger/assets/{asset['id']}/components/{gpu_component['id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["components"]) == 1
    assert body["acquisition_cost"] == "300.00"

    # The GPU line's amount is no longer capitalized — it can be re-used elsewhere.
    resp = treasurer_client.post(
        f"/api/v1/ledger/assets/{asset['id']}/components", json=_full_component(gpu),
    )
    assert resp.status_code == 201


def test_delete_last_asset_component_rejected(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00")
    asset = _capitalize(treasurer_client, entry, afa_category, name="PC")
    [component] = asset["components"]

    resp = treasurer_client.delete(f"/api/v1/ledger/assets/{asset['id']}/components/{component['id']}")
    assert resp.status_code == 400
    assert "delete the whole asset" in resp.json()["detail"]


def test_delete_asset_component_unknown_404(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00")
    asset = _capitalize(treasurer_client, entry, afa_category, name="PC")
    resp = treasurer_client.delete(f"/api/v1/ledger/assets/{asset['id']}/components/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Key Design Decision #50: Anlagenspiegel (per-year Anschaffungskosten/
# Anfangswert/Zugang/Abgang/AfA/Endwert, as needed for the Steuererklärung)
# ---------------------------------------------------------------------------

def _row_for(report, asset_id):
    return next(r for r in report["rows"] if r["asset_id"] == asset_id)


def test_anlagenspiegel_requires_auth(client):
    resp = client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2024})
    assert resp.status_code == 401


def test_anlagenspiegel_acquisition_year(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset = _capitalize(treasurer_client, entry, afa_category, useful_life_years=5)

    report = treasurer_client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2024}).json()
    row = _row_for(report, asset["id"])
    assert row["opening_book_value"] == "0.00"
    assert row["zugang"] == "1200.00"
    assert row["abgang"] == "0.00"
    assert row["afa"] == "200.00"  # 10 depreciable months at 1200/60=20/month
    assert row["closing_book_value"] == "1000.00"
    assert row["acquisition_cost_end_of_year"] == "1200.00"


def test_anlagenspiegel_full_year_after_acquisition(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset = _capitalize(treasurer_client, entry, afa_category, useful_life_years=5)

    report = treasurer_client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2025}).json()
    row = _row_for(report, asset["id"])
    assert row["opening_book_value"] == "1000.00"  # 1200 - 200 from 2024
    assert row["zugang"] == "0.00"
    assert row["afa"] == "240.00"  # full year at 20/month
    assert row["closing_book_value"] == "760.00"
    assert row["acquisition_cost_end_of_year"] == "1200.00"


def test_anlagenspiegel_excludes_asset_before_acquisition_year(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset = _capitalize(treasurer_client, entry, afa_category, useful_life_years=5)
    report = treasurer_client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2023}).json()
    assert all(r["asset_id"] != asset["id"] for r in report["rows"])


def test_anlagenspiegel_disposal_year(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-01-01")
    asset = _capitalize(treasurer_client, entry, afa_category, useful_life_years=5)
    component_id = asset["components"][0]["id"]
    treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{component_id}", json={"disposed_at": "2025-06-30"},
    )

    report = treasurer_client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2025}).json()
    row = _row_for(report, asset["id"])
    # 2024: 12 months at 1200/60=20/month -> 240.00 opening cumulative dep, opening book value 960.00
    assert row["opening_book_value"] == "960.00"
    # 2025 Jan-Jun (6 months) afa = 120.00, book value at disposal = 960 - 120 = 840.00
    assert row["afa"] == "120.00"
    assert row["abgang"] == "840.00"
    assert row["closing_book_value"] == "0.00"
    assert row["acquisition_cost_end_of_year"] == "0.00"

    # Gone before 2026 even started — no longer listed at all.
    later = treasurer_client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2026}).json()
    assert all(r["asset_id"] != asset["id"] for r in later["rows"])


def test_anlagenspiegel_multi_component_addition_shows_only_new_zugang(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00", entry_date="2024-01-01")
    asset = _capitalize(treasurer_client, case, afa_category, useful_life_years=3, name="PC")
    upgrade = _book_purchase(treasurer_client, bank_account, purchase_category, "600.00", entry_date="2025-06-01")
    treasurer_client.post(f"/api/v1/ledger/assets/{asset['id']}/components", json=_full_component(upgrade))

    report = treasurer_client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2025}).json()
    row = _row_for(report, asset["id"])
    # Only the upgrade counts as this year's Zugang — the original part was
    # already on the books before 2025 started.
    assert row["zugang"] == "600.00"
    assert row["acquisition_cost_end_of_year"] == "900.00"


def test_anlagenspiegel_disposing_one_component_keeps_asset_with_remaining_one(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    case = _book_purchase(treasurer_client, bank_account, purchase_category, "300.00", entry_date="2024-01-01")
    gpu = _book_purchase(treasurer_client, bank_account, purchase_category, "600.00", entry_date="2024-01-01")
    asset = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "PC", "components": [_full_component(case), _full_component(gpu)],
            "useful_life_years": 3, "category_id": afa_category.id,
        },
    ).json()
    gpu_component_id = next(c["id"] for c in asset["components"] if c["entry_line_id"] == _category_line_id(gpu))
    treasurer_client.put(
        f"/api/v1/ledger/assets/{asset['id']}/components/{gpu_component_id}", json={"disposed_at": "2025-12-31"},
    )

    # 2026: the GPU is gone, but the case is still on the books — the asset
    # as a whole must still be listed, just without the disposed part's cost.
    report = treasurer_client.get("/api/v1/ledger/report/anlagenspiegel", params={"year": 2026}).json()
    row = _row_for(report, asset["id"])
    assert row["acquisition_cost_end_of_year"] == "300.00"
