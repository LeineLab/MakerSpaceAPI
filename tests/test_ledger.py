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
# GET /ledger/accounts/{id}/balance
# ---------------------------------------------------------------------------

def test_account_balance_starts_at_opening_balance(treasurer_client, bank_account):
    resp = treasurer_client.get(f"/api/v1/ledger/accounts/{bank_account.id}/balance")
    assert resp.status_code == 200
    data = resp.json()
    assert data["computed_balance"] == "0.00"
    assert data["last_statement_balance"] is None


def test_account_balance_reflects_booked_entries(treasurer_client, bank_account, income_category):
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
    resp = treasurer_client.get(f"/api/v1/ledger/accounts/{bank_account.id}/balance")
    assert resp.json()["computed_balance"] == "50.00"


def test_account_balance_as_of_excludes_later_entries(treasurer_client, bank_account, income_category):
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-06-01",
            "description": "Spätere Buchung",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    resp = treasurer_client.get(f"/api/v1/ledger/accounts/{bank_account.id}/balance?as_of=2026-03-01")
    assert resp.json()["computed_balance"] == "0.00"

    resp_later = treasurer_client.get(f"/api/v1/ledger/accounts/{bank_account.id}/balance?as_of=2026-06-01")
    assert resp_later.json()["computed_balance"] == "50.00"


def test_account_balance_unknown_account_404(treasurer_client):
    resp = treasurer_client.get("/api/v1/ledger/accounts/999/balance")
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


def test_update_unused_category_can_change_kind_sphere_slug(treasurer_client, income_category):
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}",
        json={"slug": "spenden", "kind": "expense", "sphere": "zweckbetrieb"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "spenden"
    assert data["kind"] == "expense"
    assert data["sphere"] == "zweckbetrieb"


def test_update_used_category_cannot_change_kind(
    treasurer_client, bank_account, income_category
):
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

    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"kind": "expense"}
    )
    assert resp.status_code == 409

    # name/active stay changeable regardless of usage
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"name": "Umbenannt"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Umbenannt"


def test_update_category_sphere_rejected_when_spheres_disabled(treasurer_client, income_category, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "LEDGER_SPHERES_ENABLED", False)
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"sphere": "ideell"}
    )
    assert resp.status_code == 400


def test_update_category_slug_conflicts_with_another_category(treasurer_client, income_category, expense_category):
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"slug": expense_category.slug}
    )
    assert resp.status_code == 409


def test_list_categories_excludes_inactive_by_default(treasurer_client, income_category):
    treasurer_client.put(f"/api/v1/ledger/categories/{income_category.id}", json={"active": False})
    resp = treasurer_client.get("/api/v1/ledger/categories")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# match_keywords (Key Design Decision #45) — purpose-text autofill suggestions
# ---------------------------------------------------------------------------

def test_create_category_with_match_keywords(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={
            "name": "Mitgliedsbeiträge", "slug": "mitgliedsbeitraege", "kind": "income",
            "sphere": "ideell", "match_keywords": ["Mitgliedsbeitrag", "Beitrag"],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["match_keywords"] == ["Mitgliedsbeitrag", "Beitrag"]


def test_create_category_match_keywords_deduped_case_insensitively(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={
            "name": "Spenden", "slug": "spenden", "kind": "income", "sphere": "ideell",
            "match_keywords": ["Spende", " spende ", "SPENDE", ""],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["match_keywords"] == ["Spende"]


def test_create_category_match_keyword_overlapping_existing_rejected(treasurer_client):
    treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Keksdose", "slug": "keksdose", "kind": "expense", "sphere": "ideell",
              "match_keywords": ["Keksdose"]},
    )
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Kekse", "slug": "kekse", "kind": "expense", "sphere": "ideell",
              "match_keywords": ["Keks"]},
    )
    assert resp.status_code == 400


def test_create_category_match_keyword_containing_existing_rejected(treasurer_client):
    """Same overlap check, the other direction: registering the longer term
    first, then a shorter one that would be its substring."""
    treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Kekse", "slug": "kekse", "kind": "expense", "sphere": "ideell",
              "match_keywords": ["Keks"]},
    )
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Keksdose", "slug": "keksdose", "kind": "expense", "sphere": "ideell",
              "match_keywords": ["Keksdose"]},
    )
    assert resp.status_code == 400


def test_create_category_match_keyword_no_conflict_when_distinct(treasurer_client):
    treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Spenden", "slug": "spenden", "kind": "income", "sphere": "ideell",
              "match_keywords": ["Spende"]},
    )
    resp = treasurer_client.post(
        "/api/v1/ledger/categories",
        json={"name": "Mitgliedsbeiträge", "slug": "mitgliedsbeitraege", "kind": "income",
              "sphere": "ideell", "match_keywords": ["Mitgliedsbeitrag"]},
    )
    assert resp.status_code == 201


def test_update_category_match_keywords_replace(treasurer_client, income_category):
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}",
        json={"match_keywords": ["Foo", "Bar"]},
    )
    assert resp.status_code == 200
    assert resp.json()["match_keywords"] == ["Foo", "Bar"]


def test_update_category_match_keywords_empty_list_clears(treasurer_client, income_category):
    treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"match_keywords": ["Foo"]}
    )
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"match_keywords": []}
    )
    assert resp.status_code == 200
    assert resp.json()["match_keywords"] is None


def test_update_category_match_keywords_allowed_even_when_in_use(
    treasurer_client, bank_account, income_category
):
    """Unlike slug/kind/sphere, match_keywords never reclassifies past
    bookings, so it's editable regardless of whether the category is used."""
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
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"match_keywords": ["Beitrag"]}
    )
    assert resp.status_code == 200
    assert resp.json()["match_keywords"] == ["Beitrag"]


def test_update_category_match_keyword_overlapping_other_category_rejected(
    treasurer_client, income_category, expense_category
):
    treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"match_keywords": ["Keksdose"]}
    )
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{expense_category.id}", json={"match_keywords": ["Keks"]}
    )
    assert resp.status_code == 400


def test_update_category_match_keywords_no_conflict_with_own_previous_value(
    treasurer_client, income_category
):
    """Re-submitting/adjusting a category's own keywords must not trip the
    overlap check against its own prior value."""
    treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}", json={"match_keywords": ["Beitrag"]}
    )
    resp = treasurer_client.put(
        f"/api/v1/ledger/categories/{income_category.id}",
        json={"match_keywords": ["Beitrag", "Mitgliedsbeitrag"]},
    )
    assert resp.status_code == 200

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


def test_create_entry_multiple_invoices_each_link_own_document(
    treasurer_client, bank_account, expense_category, db
):
    """Key Design Decision #44: several invoices paid in one bank debit each
    get their own paperless_document_id on their own category line, instead
    of sharing the one entry-level field the old schema had."""
    other_expense = LedgerCategory(
        name="Büromaterial", slug="bueromaterial-2",
        kind=LedgerCategoryKind.expense, sphere=LedgerSphere.ideell, active=True,
    )
    db.add(other_expense)
    db.commit()

    resp = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-02",
            "description": "Sammelabbuchung 2 Rechnungen",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "-100.00"},
                {"category_id": expense_category.id, "amount": "70.00", "paperless_document_id": "INV-1"},
                {"category_id": other_expense.id, "amount": "30.00", "paperless_document_id": "INV-2"},
            ],
        },
    )
    assert resp.status_code == 201
    lines = resp.json()["lines"]
    bank_line = next(l for l in lines if l["bank_account_id"] == bank_account.id)
    cat_lines = {l["category_id"]: l["paperless_document_id"] for l in lines if l["category_id"] is not None}
    assert bank_line["paperless_document_id"] is None
    assert cat_lines[expense_category.id] == "INV-1"
    assert cat_lines[other_expense.id] == "INV-2"


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


def test_list_entries_filters_by_paperless_document_id(treasurer_client, bank_account, income_category):
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Mit Beleg",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00", "paperless_document_id": "42"},
            ],
        },
    )
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-02",
            "description": "Ohne Beleg",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "10.00"},
                {"category_id": income_category.id, "amount": "-10.00"},
            ],
        },
    )

    resp = treasurer_client.get("/api/v1/ledger/entries?paperless_document_id=42")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["description"] == "Mit Beleg"

    assert treasurer_client.get("/api/v1/ledger/entries?paperless_document_id=999").json() == []


def test_list_entries_filters_by_bank_account_id(treasurer_client, db, bank_account, income_category):
    other = BankAccount(iban="DE89370400440532013000", name="Zweitkonto", opening_balance=Decimal("0.00"))
    db.add(other)
    db.commit()
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01", "description": "Auf Vereinskonto",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-02", "description": "Auf Zweitkonto",
            "lines": [
                {"bank_account_id": other.id, "amount": "10.00"},
                {"category_id": income_category.id, "amount": "-10.00"},
            ],
        },
    )

    resp = treasurer_client.get(f"/api/v1/ledger/entries?bank_account_id={other.id}")
    assert resp.status_code == 200
    [entry] = resp.json()
    assert entry["description"] == "Auf Zweitkonto"


def test_list_entries_matched_import_lines_empty_for_manual_booking(treasurer_client, bank_account, income_category):
    treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01", "description": "Manuell gebucht",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00"},
            ],
        },
    )
    [entry] = treasurer_client.get("/api/v1/ledger/entries").json()
    assert entry["matched_import_lines"] == []


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


def test_list_entries_reports_total_count_and_pages(treasurer_client, bank_account, income_category):
    for i in range(3):
        treasurer_client.post(
            "/api/v1/ledger/entries",
            json={
                "entry_date": "2026-03-01",
                "description": f"Beitrag {i}",
                "lines": [
                    {"bank_account_id": bank_account.id, "amount": "10.00"},
                    {"category_id": income_category.id, "amount": "-10.00"},
                ],
            },
        )

    resp = treasurer_client.get("/api/v1/ledger/entries?limit=2")
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "3"
    assert len(resp.json()) == 2

    second_page = treasurer_client.get("/api/v1/ledger/entries?limit=2&offset=2")
    assert second_page.headers["X-Total-Count"] == "3"
    assert len(second_page.json()) == 1


# ---------------------------------------------------------------------------
# Entry reversal ("undo" a booking without editing/deleting anything)
# ---------------------------------------------------------------------------

def _create_entry(client, bank_account, income_category, amount="50.00", description="Beitrag"):
    resp = client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": description,
            "lines": [
                {"bank_account_id": bank_account.id, "amount": amount},
                {"category_id": income_category.id, "amount": str(-Decimal(amount))},
            ],
        },
    )
    assert resp.status_code == 201
    return resp.json()


def test_reverse_entry_posts_offsetting_entry(treasurer_client, bank_account, income_category):
    original = _create_entry(treasurer_client, bank_account, income_category, amount="50.00")

    resp = treasurer_client.post(f"/api/v1/ledger/entries/{original['id']}/reverse")
    assert resp.status_code == 201
    reversal = resp.json()

    assert reversal["reverses_entry_id"] == original["id"]
    assert reversal["description"] == f"Storno: {original['description']}"
    amounts = {Decimal(str(l["amount"])) for l in reversal["lines"]}
    assert amounts == {Decimal("-50.00"), Decimal("50.00")}

    # the original entry is untouched — immutability preserved
    all_entries = treasurer_client.get("/api/v1/ledger/entries").json()
    original_entry = next(e for e in all_entries if e["id"] == original["id"])
    original_amounts = {Decimal(str(l["amount"])) for l in original_entry["lines"]}
    assert original_amounts == {Decimal("-50.00"), Decimal("50.00")}


def test_reverse_entry_copies_line_paperless_document(treasurer_client, bank_account, income_category):
    resp = treasurer_client.post(
        "/api/v1/ledger/entries",
        json={
            "entry_date": "2026-03-01",
            "description": "Mit Beleg",
            "lines": [
                {"bank_account_id": bank_account.id, "amount": "50.00"},
                {"category_id": income_category.id, "amount": "-50.00", "paperless_document_id": "INV-9"},
            ],
        },
    )
    original = resp.json()

    reversal = treasurer_client.post(f"/api/v1/ledger/entries/{original['id']}/reverse").json()
    cat_line = next(l for l in reversal["lines"] if l["category_id"] == income_category.id)
    assert cat_line["paperless_document_id"] == "INV-9"


def test_reverse_entry_cancels_out_in_euer_report(treasurer_client, bank_account, income_category):
    original = _create_entry(treasurer_client, bank_account, income_category, amount="50.00")
    treasurer_client.post(f"/api/v1/ledger/entries/{original['id']}/reverse")

    report = treasurer_client.get("/api/v1/ledger/report/euer?year=2026").json()
    assert report["total_income"] == "0.00"


def test_reverse_entry_twice_conflicts(treasurer_client, bank_account, income_category):
    original = _create_entry(treasurer_client, bank_account, income_category)
    first = treasurer_client.post(f"/api/v1/ledger/entries/{original['id']}/reverse")
    assert first.status_code == 201

    second = treasurer_client.post(f"/api/v1/ledger/entries/{original['id']}/reverse")
    assert second.status_code == 409


def test_reverse_unknown_entry_404(treasurer_client):
    resp = treasurer_client.post("/api/v1/ledger/entries/999/reverse")
    assert resp.status_code == 404


def test_cannot_reverse_a_reversal(treasurer_client, bank_account, income_category):
    """A Storno's ledger_target_payout_entries/matched_entry_id links only ever
    live on the original entry and are cleared once reversed — reversing the
    reversal would re-instate the original's financial effect without
    re-establishing that link, risking a double-booking down the line. The
    correct way to "undo an accidental reversal" is to re-book normally
    (the original booking/staging line is already reopened for that)."""
    original = _create_entry(treasurer_client, bank_account, income_category)
    reversal = treasurer_client.post(f"/api/v1/ledger/entries/{original['id']}/reverse").json()

    resp = treasurer_client.post(f"/api/v1/ledger/entries/{reversal['id']}/reverse")
    assert resp.status_code == 400


def test_auditor_cannot_reverse_entry(auditor_client, treasurer_client, bank_account, income_category):
    original = _create_entry(treasurer_client, bank_account, income_category)
    resp = auditor_client.post(f"/api/v1/ledger/entries/{original['id']}/reverse")
    assert resp.status_code in (401, 403)


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
