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
def income_category(db):
    c = LedgerCategory(
        name="Mitgliedsbeiträge", slug="mitgliedsbeitraege",
        kind=LedgerCategoryKind.income, sphere=LedgerSphere.ideell, active=True,
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


# ---------------------------------------------------------------------------
# GET/POST /ledger/accounts
# ---------------------------------------------------------------------------

def test_list_accounts_requires_auth(client):
    resp = client.get("/api/v1/ledger/accounts")
    assert resp.status_code == 401


def test_auditor_can_list_accounts(auditor_client, bank_account):
    resp = auditor_client.get("/api/v1/ledger/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["iban"] == "DE02120300000000202051"


def test_auditor_cannot_create_account(auditor_client):
    resp = auditor_client.post(
        "/api/v1/ledger/accounts",
        json={"iban": "DE89370400440532013000", "name": "Privatkonto"},
    )
    assert resp.status_code in (401, 403)


def test_treasurer_can_create_account(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/accounts",
        json={"iban": "DE89370400440532013000", "name": "Vereinskonto"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["tracked"] is True
    assert Decimal(str(data["opening_balance"])) == Decimal("0.00")


def test_create_account_duplicate_iban_conflicts(treasurer_client, bank_account):
    resp = treasurer_client.post(
        "/api/v1/ledger/accounts",
        json={"iban": bank_account.iban, "name": "Zweitzugang auf gleiches Konto"},
    )
    assert resp.status_code == 409


def test_update_account_can_untrack(treasurer_client, bank_account):
    resp = treasurer_client.put(
        f"/api/v1/ledger/accounts/{bank_account.id}",
        json={"tracked": False},
    )
    assert resp.status_code == 200
    assert resp.json()["tracked"] is False


# ---------------------------------------------------------------------------
# Offline accounts (manual-balance holders, e.g. a domain registrar credit)
# ---------------------------------------------------------------------------

def test_create_offline_account_without_iban(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/accounts",
        json={"name": "Domain-Guthaben", "is_offline": True},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_offline"] is True
    assert data["iban"] is None


def test_create_offline_account_with_iban_rejected(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/accounts",
        json={"name": "Bad", "is_offline": True, "iban": "DE02120300000000202051"},
    )
    assert resp.status_code == 400


def test_create_online_account_without_iban_rejected(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/accounts",
        json={"name": "Bad"},
    )
    assert resp.status_code == 400


def test_offline_account_usable_as_transfer_target(treasurer_client, bank_account):
    """The whole point: move money from a real account into an offline one,
    then later book an expense out of it — with zero category lines on the
    transfer itself, so it never inflates the EÜR."""
    offline = treasurer_client.post(
        "/api/v1/ledger/accounts",
        json={"name": "Domain-Guthaben", "is_offline": True},
    ).json()

    transfer = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Aufladung Domain-Guthaben",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "-50.00"},
                {"bank_account_id": offline["id"], "amount": "50.00"},
            ],
        },
    )
    assert transfer.status_code == 201
    assert len(transfer.json()["lines"]) == 2

    report = treasurer_client.get("/api/v1/ledger/report/euer?year=2026").json()
    assert report["categories"] == []
    assert report["total_income"] == "0.00"
    assert report["total_expense"] == "0.00"


def test_update_unknown_account_404(treasurer_client):
    resp = treasurer_client.put("/api/v1/ledger/accounts/999", json={"tracked": False})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET/POST /ledger/categories
# ---------------------------------------------------------------------------

def test_create_category(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Spenden", "slug": "spenden", "kind": "income", "sphere": "ideell"},
    )
    assert resp.status_code == 201
    assert resp.json()["slug"] == "spenden"


def test_create_category_without_sphere_rejected_when_enabled(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Spenden", "slug": "spenden", "kind": "income"},
    )
    assert resp.status_code == 400


def test_create_category_without_sphere_allowed_when_disabled(treasurer_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "LEDGER_SPHERES_ENABLED", False)
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Spenden", "slug": "spenden", "kind": "income", "sphere": "ideell"},
    )
    assert resp.status_code == 201
    # sphere is ignored/forced to None while the feature is disabled
    assert resp.json()["sphere"] is None


def test_ledger_config_reflects_setting(treasurer_client, monkeypatch):
    from app.config import settings

    resp = treasurer_client.get("/api/v1/ledger/config")
    assert resp.status_code == 200
    assert resp.json()["spheres_enabled"] is True

    monkeypatch.setattr(settings, "LEDGER_SPHERES_ENABLED", False)
    resp = treasurer_client.get("/api/v1/ledger/config")
    assert resp.json()["spheres_enabled"] is False


def test_create_category_duplicate_slug_conflicts(treasurer_client, income_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Andere", "slug": income_category.slug, "kind": "income", "sphere": "ideell"},
    )
    assert resp.status_code == 409


def test_list_categories_excludes_inactive_by_default(treasurer_client, income_category):
    treasurer_client.put(f"/api/v1/ledger/categories/{income_category.id}", json={"active": False})
    resp = treasurer_client.get("/api/v1/ledger/categories")
    assert resp.status_code == 200
    assert resp.json() == []

    resp_all = treasurer_client.get("/api/v1/ledger/categories?include_inactive=true")
    assert len(resp_all.json()) == 1


# ---------------------------------------------------------------------------
# POST /ledger/entries — double-entry booking with splitting
# ---------------------------------------------------------------------------

def test_create_entry_success(treasurer_client, bank_account, income_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Mitgliedsbeitrag Max Mustermann",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["lines"]) == 2
    assert data["created_by"] == "test-treasurer-sub"


def test_create_entry_splits_across_multiple_categories(
    treasurer_client, bank_account, expense_category, db
):
    other_expense = LedgerCategory(
        name="Büromaterial", slug="bueromaterial",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(other_expense)
    db.commit()

    resp = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-02",
            "description": "Sammelbeleg Baumarkt",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "-100.00"},
                {"category_id": expense_category.id, "amount": "70.00"},
                {"category_id": other_expense.id, "amount": "30.00"},
            ],
        },
    )
    assert resp.status_code == 201
    assert len(resp.json()["lines"]) == 3


def test_create_entry_rejects_unbalanced_lines(treasurer_client, bank_account, income_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Falsch",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-40.00"},
            ],
        },
    )
    assert resp.status_code == 400


def test_create_entry_rejects_line_with_both_refs(treasurer_client, bank_account, income_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Falsch",
            "lines": [
                {
                    "bank_account_id": bank_account.id,
                    "category_id": income_category.id,
                    "amount": "50.00",
                },
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    assert resp.status_code == 400


def test_create_entry_rejects_unknown_account(treasurer_client, income_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Falsch",
            "lines": [
                {"bank_account_id": 999, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    assert resp.status_code == 404


def test_auditor_cannot_create_entry(auditor_client, bank_account, income_category):
    resp = auditor_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Sollte scheitern",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    assert resp.status_code in (401, 403)


def test_auditor_can_list_entries(auditor_client, treasurer_client, bank_account, income_category):
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Beitrag",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    resp = auditor_client.get("/api/v1/ledger/entries")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# GET /ledger/report/euer
# ---------------------------------------------------------------------------

def test_euer_report_signs_are_intuitive(treasurer_client, bank_account, income_category, expense_category):
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Beitrag",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-02",
            "description": "Einkauf",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "-30.00"},
                {"category_id": expense_category.id, "amount": "30.00"},
            ],
        },
    )

    resp = treasurer_client.get("/api/v1/ledger/report/euer?year=2026")
    assert resp.status_code == 200
    data = resp.json()
    assert Decimal(str(data["total_income"])) == Decimal("50.00")
    assert Decimal(str(data["total_expense"])) == Decimal("30.00")
    assert Decimal(str(data["net_result"])) == Decimal("20.00")

    by_slug = {c["slug"]: Decimal(str(c["total"])) for c in data["categories"]}
    assert by_slug["mitgliedsbeitraege"] == Decimal("50.00")
    assert by_slug["wareneinkauf"] == Decimal("30.00")


def test_euer_report_excludes_other_years(treasurer_client, bank_account, income_category):
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2025-01-01",
            "description": "Letztes Jahr",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    resp = treasurer_client.get("/api/v1/ledger/report/euer?year=2026")
    assert resp.status_code == 200
    assert resp.json()["categories"] == []


def test_euer_report_pdf_export(treasurer_client, bank_account, income_category, expense_category):
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Beitrag",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    resp = treasurer_client.get("/api/v1/ledger/report/euer/pdf?year=2026&lang=de")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


def test_euer_report_pdf_export_empty_year(treasurer_client):
    """Must not crash when there's nothing to report (no categories booked)."""
    resp = treasurer_client.get("/api/v1/ledger/report/euer/pdf?year=1999&lang=en")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
