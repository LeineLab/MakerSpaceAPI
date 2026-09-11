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
            "name": "Lasercutter", "entry_line_id": _category_line_id(entry),
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code in (401, 403)


def test_create_asset_success(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter Speedy 400", "entry_line_id": _category_line_id(entry),
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


def test_create_asset_explicit_acquisition_date_overrides_entry_date(
    treasurer_client, bank_account, purchase_category, afa_category,
):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "entry_line_id": _category_line_id(entry),
            "useful_life_years": 5, "category_id": afa_category.id,
            "acquisition_date": "2024-03-01",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["acquisition_date"] == "2024-03-01"


def test_create_asset_rejects_bank_side_line(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    [bank_line] = [l for l in entry["lines"] if l["bank_account_id"] is not None]
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "entry_line_id": bank_line["id"],
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
            "name": "Lasercutter", "entry_line_id": _category_line_id(entry),
            "useful_life_years": 5, "category_id": afa_category.id,
        },
    )
    assert resp.status_code == 400


def test_create_asset_rejects_afa_category_that_is_income(treasurer_client, bank_account, purchase_category, income_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={
            "name": "Lasercutter", "entry_line_id": _category_line_id(entry),
            "useful_life_years": 5, "category_id": income_category.id,
        },
    )
    assert resp.status_code == 400


def test_create_asset_rejects_already_linked_line(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    line_id = _category_line_id(entry)
    first = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "entry_line_id": line_id, "useful_life_years": 5, "category_id": afa_category.id},
    )
    assert first.status_code == 201
    second = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter (Dopplung)", "entry_line_id": line_id, "useful_life_years": 5, "category_id": afa_category.id},
    )
    assert second.status_code == 409


def test_create_asset_unknown_entry_line_404(treasurer_client, afa_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "entry_line_id": 9999, "useful_life_years": 5, "category_id": afa_category.id},
    )
    assert resp.status_code == 404


def test_create_asset_unknown_category_404(treasurer_client, bank_account, purchase_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00")
    resp = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": 9999},
    )
    assert resp.status_code == 404


def test_list_assets_includes_computed_book_value(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": afa_category.id},
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
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": afa_category.id},
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
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": afa_category.id},
    ).json()["id"]
    resp = treasurer_client.put(f"/api/v1/ledger/assets/{asset_id}", json={"category_id": income_category.id})
    assert resp.status_code == 400


def test_update_asset_dispose_and_clear(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset_id = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": afa_category.id},
    ).json()["id"]

    resp = treasurer_client.put(f"/api/v1/ledger/assets/{asset_id}", json={"disposed_at": "2025-06-30"})
    assert resp.status_code == 200
    assert resp.json()["disposed_at"] == "2025-06-30"

    resp = treasurer_client.put(f"/api/v1/ledger/assets/{asset_id}", json={"clear_disposed_at": True})
    assert resp.status_code == 200
    assert resp.json()["disposed_at"] is None


def test_update_asset_dispose_before_acquisition_rejected(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset_id = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": afa_category.id},
    ).json()["id"]
    resp = treasurer_client.put(f"/api/v1/ledger/assets/{asset_id}", json={"disposed_at": "2024-01-01"})
    assert resp.status_code == 400


def test_update_unknown_asset_404(treasurer_client):
    resp = treasurer_client.put("/api/v1/ledger/assets/9999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_asset_reverts_to_normal_expense(treasurer_client, bank_account, purchase_category, afa_category):
    entry = _book_purchase(treasurer_client, bank_account, purchase_category, "1200.00", entry_date="2024-03-15")
    asset_id = treasurer_client.post(
        "/api/v1/ledger/assets",
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": afa_category.id},
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
        json={"name": "Lasercutter", "entry_line_id": _category_line_id(entry), "useful_life_years": 5, "category_id": afa_category.id},
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
            "name": name, "entry_line_id": _category_line_id(entry),
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
    treasurer_client.put(f"/api/v1/ledger/assets/{asset['id']}", json={"disposed_at": "2025-06-30"})

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
