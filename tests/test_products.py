from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.product import Product, ProductAlias, ProductAudit, ProductAuditType
from app.models.transaction import Transaction, TransactionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def product(db):
    """An active product with stock."""
    p = Product(
        ean="1234567890123",
        name="Club Mate",
        price=Decimal("1.50"),
        stock=10,
        category="drinks",
        active=True,
    )
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def inactive_product(db):
    p = Product(
        ean="0000000000001",
        name="Old Product",
        price=Decimal("0.50"),
        stock=5,
        category="misc",
        active=False,
    )
    db.add(p)
    db.commit()
    return p


# ---------------------------------------------------------------------------
# GET /products — public
# ---------------------------------------------------------------------------

def test_list_products_empty(client):
    resp = client.get("/api/v1/products")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_products_returns_active_only(client, product, inactive_product):
    resp = client.get("/api/v1/products")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ean"] == product.ean


def test_list_products_filter_by_category(client, product, db):
    db.add(Product(ean="9999999999999", name="Screw", price=Decimal("0.10"), stock=100, category="hardware"))
    db.commit()

    resp = client.get("/api/v1/products?category=drinks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ean"] == product.ean


def test_list_products_sorted_by_category_then_name(client, db):
    db.add_all([
        Product(ean="A", name="Z Item", price=Decimal("1.00"), stock=1, category="b-cat"),
        Product(ean="B", name="A Item", price=Decimal("1.00"), stock=1, category="a-cat"),
    ])
    db.commit()

    data = client.get("/api/v1/products").json()
    assert data[0]["category"] == "a-cat"
    assert data[1]["category"] == "b-cat"


# ---------------------------------------------------------------------------
# GET /products.json — endpoint removed, now use /products
# ---------------------------------------------------------------------------

def test_list_products_json(client, product):
    resp = client.get("/api/v1/products.json")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /categories — public
# ---------------------------------------------------------------------------

def test_categories_empty(client):
    resp = client.get("/api/v1/categories")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_categories(client, product, db):
    db.add(Product(ean="B", name="Other", price=Decimal("1.00"), stock=1, category="snacks"))
    db.commit()

    data = client.get("/api/v1/categories").json()
    assert data == sorted(data)
    assert "drinks" in data and "snacks" in data


def test_categories_excludes_inactive(client, inactive_product):
    assert client.get("/api/v1/categories").json() == []


# ---------------------------------------------------------------------------
# GET /products/{ean} — public
# ---------------------------------------------------------------------------

def test_get_product_by_ean(client, product):
    resp = client.get(f"/api/v1/products/{product.ean}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ean"] == product.ean
    assert data["aliases"] == []


def test_get_product_by_alias(client, product, db):
    db.add(ProductAlias(ean="ALIAS001", product_id=product.id))
    db.commit()

    resp = client.get("/api/v1/products/ALIAS001")
    assert resp.status_code == 200
    assert resp.json()["ean"] == product.ean


def test_get_product_not_found(client):
    assert client.get("/api/v1/products/NOTEXIST").status_code == 404


# ---------------------------------------------------------------------------
# POST /products — admin only
# ---------------------------------------------------------------------------

def test_create_product_success(admin_client):
    resp = admin_client.post(
        "/api/v1/products",
        json={"ean": "1111111111111", "name": "Fritz Kola", "price": "1.80", "stock": 20, "category": "drinks"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["ean"] == "1111111111111"
    assert data["active"] is True


def test_create_product_writes_audit(admin_client, db):
    admin_client.post(
        "/api/v1/products",
        json={"ean": "2222222222222", "name": "Test", "price": "1.00", "stock": 0, "category": "misc"},
    )
    audit = db.query(ProductAudit).filter(ProductAudit.change_type == ProductAuditType.created).first()
    assert audit is not None
    assert audit.changed_by == "test-admin-sub"


def test_create_product_duplicate_ean(admin_client, product):
    resp = admin_client.post(
        "/api/v1/products",
        json={"ean": product.ean, "name": "Dupe", "price": "1.00", "stock": 0, "category": "misc"},
    )
    assert resp.status_code == 409


def test_create_product_requires_admin(client):
    resp = client.post(
        "/api/v1/products",
        json={"ean": "3333333333333", "name": "X", "price": "1.00", "stock": 0, "category": "misc"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /products/{ean} — admin only
# ---------------------------------------------------------------------------

def test_update_product_name(admin_client, product, db):
    resp = admin_client.put(f"/api/v1/products/{product.ean}", json={"name": "Bio Mate"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Bio Mate"

    audit = db.query(ProductAudit).filter(ProductAudit.change_type == ProductAuditType.name_change).first()
    assert audit is not None
    assert audit.old_value == "Club Mate"
    assert audit.new_value == "Bio Mate"


def test_update_product_price(admin_client, product, db):
    admin_client.put(f"/api/v1/products/{product.ean}", json={"price": "2.00"})

    audit = db.query(ProductAudit).filter(ProductAudit.change_type == ProductAuditType.price_change).first()
    assert audit is not None


def test_update_product_category(admin_client, product, db):
    admin_client.put(f"/api/v1/products/{product.ean}", json={"category": "snacks"})

    audit = db.query(ProductAudit).filter(ProductAudit.change_type == ProductAuditType.category_change).first()
    assert audit is not None


def test_update_product_deactivate(admin_client, product, db):
    resp = admin_client.put(f"/api/v1/products/{product.ean}", json={"active": False})
    assert resp.json()["active"] is False

    assert db.query(ProductAudit).filter(ProductAudit.change_type == ProductAuditType.deactivated).first() is not None


def test_update_product_no_audit_when_unchanged(admin_client, product, db):
    """Sending the same value should not create an audit row."""
    admin_client.put(f"/api/v1/products/{product.ean}", json={"name": product.name})
    assert db.query(ProductAudit).count() == 0


def test_update_product_not_found(admin_client):
    assert admin_client.put("/api/v1/products/NOTEXIST", json={"name": "X"}).status_code == 404


def test_update_product_via_alias(admin_client, product, db):
    db.add(ProductAlias(ean="ALIAS002", product_id=product.id))
    db.commit()

    resp = admin_client.put("/api/v1/products/ALIAS002", json={"name": "Via Alias"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Via Alias"


# ---------------------------------------------------------------------------
# POST /products/{ean}/stock — admin only
# ---------------------------------------------------------------------------

def test_adjust_stock_add(admin_client, product, db):
    resp = admin_client.post(f"/api/v1/products/{product.ean}/stock", json={"delta": 5})
    assert resp.status_code == 200
    assert resp.json()["stock"] == 15

    audit = db.query(ProductAudit).filter(ProductAudit.change_type == ProductAuditType.stock_add).first()
    assert audit.old_value == "10" and audit.new_value == "15"


def test_adjust_stock_deduct(admin_client, product):
    resp = admin_client.post(f"/api/v1/products/{product.ean}/stock", json={"delta": -3})
    assert resp.status_code == 200
    assert resp.json()["stock"] == 7


def test_adjust_stock_below_zero(admin_client, product):
    assert admin_client.post(f"/api/v1/products/{product.ean}/stock", json={"delta": -99}).status_code == 400


def test_adjust_stock_not_found(admin_client):
    assert admin_client.post("/api/v1/products/NOTEXIST/stock", json={"delta": 1}).status_code == 404


# ---------------------------------------------------------------------------
# POST /products/{ean}/stocktaking — admin only
# ---------------------------------------------------------------------------

def test_stocktaking_sets_absolute(admin_client, product, db):
    resp = admin_client.post(f"/api/v1/products/{product.ean}/stocktaking", json={"count": 42})
    assert resp.status_code == 200
    assert resp.json()["stock"] == 42

    audit = db.query(ProductAudit).filter(ProductAudit.change_type == ProductAuditType.stocktaking).first()
    assert audit.old_value == "10" and audit.new_value == "42"


def test_stocktaking_negative_count(admin_client, product):
    assert admin_client.post(f"/api/v1/products/{product.ean}/stocktaking", json={"count": -1}).status_code == 400


# ---------------------------------------------------------------------------
# GET /products/{ean}/audit — admin only
# ---------------------------------------------------------------------------

def test_get_audit_empty(admin_client, product):
    resp = admin_client.get(f"/api/v1/products/{product.ean}/audit")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_audit_after_changes(admin_client, product):
    admin_client.put(f"/api/v1/products/{product.ean}", json={"name": "New Name"})
    admin_client.put(f"/api/v1/products/{product.ean}", json={"price": "2.00"})

    data = admin_client.get(f"/api/v1/products/{product.ean}/audit").json()
    assert len(data) == 2
    # Newest first
    assert data[0]["change_type"] == ProductAuditType.price_change


def test_get_audit_requires_admin(client, product):
    assert client.get(f"/api/v1/products/{product.ean}/audit").status_code == 401


# ---------------------------------------------------------------------------
# GET /products/{ean}/popularity — admin only
# ---------------------------------------------------------------------------

def test_popularity_zero(admin_client, product):
    data = admin_client.get(f"/api/v1/products/{product.ean}/popularity").json()
    assert data["purchase_count"] == 0
    assert data["days"] == 7


def test_popularity_counts_recent_purchases(admin_client, product, test_user, db):
    for _ in range(2):
        db.add(Transaction(
            user_id=test_user.id, amount=-product.price,
            type=TransactionType.purchase, product_id=product.id,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        ))
    # Old purchase outside 7-day window
    db.add(Transaction(
        user_id=test_user.id, amount=-product.price,
        type=TransactionType.purchase, product_id=product.id,
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10),
    ))
    db.commit()

    assert admin_client.get(f"/api/v1/products/{product.ean}/popularity?days=7").json()["purchase_count"] == 2


def test_popularity_custom_days(admin_client, product, test_user, db):
    db.add(Transaction(
        user_id=test_user.id, amount=-product.price,
        type=TransactionType.purchase, product_id=product.id,
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10),
    ))
    db.commit()

    assert admin_client.get(f"/api/v1/products/{product.ean}/popularity?days=7").json()["purchase_count"] == 0
    assert admin_client.get(f"/api/v1/products/{product.ean}/popularity?days=30").json()["purchase_count"] == 1


# ---------------------------------------------------------------------------
# GET /products/{ean}/aliases — public
# ---------------------------------------------------------------------------

def test_list_aliases_empty(client, product):
    assert client.get(f"/api/v1/products/{product.ean}/aliases").json() == []


def test_list_aliases(client, product, db):
    db.add(ProductAlias(ean="ALI001", product_id=product.id))
    db.commit()

    data = client.get(f"/api/v1/products/{product.ean}/aliases").json()
    assert data[0]["ean"] == "ALI001"


# ---------------------------------------------------------------------------
# POST /products/{ean}/aliases — admin only
# ---------------------------------------------------------------------------

def test_add_alias_success(admin_client, product, db):
    resp = admin_client.post(f"/api/v1/products/{product.ean}/aliases", json={"ean": "NEW_ALIAS"})
    assert resp.status_code == 201
    assert resp.json()["ean"] == "NEW_ALIAS"
    assert db.query(ProductAlias).filter(ProductAlias.ean == "NEW_ALIAS").first() is not None


def test_add_alias_conflicts_with_primary_ean(admin_client, product, db):
    db.add(Product(ean="OTHER_EAN", name="Other", price=Decimal("1.00"), stock=0, category="misc"))
    db.commit()

    assert admin_client.post(
        f"/api/v1/products/{product.ean}/aliases", json={"ean": "OTHER_EAN"}
    ).status_code == 409


def test_add_alias_conflicts_with_existing_alias(admin_client, product, db):
    db.add(ProductAlias(ean="TAKEN", product_id=product.id))
    db.commit()

    assert admin_client.post(
        f"/api/v1/products/{product.ean}/aliases", json={"ean": "TAKEN"}
    ).status_code == 409


def test_add_alias_requires_admin(client, product):
    assert client.post(f"/api/v1/products/{product.ean}/aliases", json={"ean": "X"}).status_code == 401


# ---------------------------------------------------------------------------
# DELETE /products/{ean}/aliases/{alias_ean} — admin only
# ---------------------------------------------------------------------------

def test_delete_alias_success(admin_client, product, db):
    db.add(ProductAlias(ean="TO_DELETE", product_id=product.id))
    db.commit()

    resp = admin_client.delete(f"/api/v1/products/{product.ean}/aliases/TO_DELETE")
    assert resp.status_code == 200
    assert db.query(ProductAlias).filter(ProductAlias.ean == "TO_DELETE").first() is None


def test_delete_alias_not_found(admin_client, product):
    assert admin_client.delete(f"/api/v1/products/{product.ean}/aliases/GHOST").status_code == 404


def test_delete_alias_wrong_product(admin_client, product, db):
    other = Product(ean="OTHER_PROD", name="Other", price=Decimal("1.00"), stock=0, category="misc")
    db.add(other)
    db.flush()
    db.add(ProductAlias(ean="ALIAS_OTHER", product_id=other.id))
    db.commit()

    assert admin_client.delete(f"/api/v1/products/{product.ean}/aliases/ALIAS_OTHER").status_code == 404


# ---------------------------------------------------------------------------
# POST /products/{ean}/purchase — checkout device only
# ---------------------------------------------------------------------------

def test_purchase_success(client, checkout_token, product, test_user, db):
    token, _ = checkout_token
    balance_before = test_user.balance
    stock_before = product.stock

    resp = client.post(
        f"/api/v1/products/{product.ean}/purchase",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["detail"] == "Purchase successful"

    db.refresh(test_user)
    db.refresh(product)
    assert test_user.balance == balance_before - product.price
    assert product.stock == stock_before - 1

    tx = db.query(Transaction).filter(
        Transaction.user_id == test_user.id,
        Transaction.type == TransactionType.purchase,
    ).first()
    assert tx is not None
    assert tx.amount == -product.price
    assert tx.product_id == product.id


def test_purchase_via_alias(client, checkout_token, product, test_user, db):
    db.add(ProductAlias(ean="ALIAS_PURCHASE", product_id=product.id))
    db.commit()
    token, _ = checkout_token

    resp = client.post(
        "/api/v1/products/ALIAS_PURCHASE/purchase",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_purchase_user_not_found(client, checkout_token, product):
    token, _ = checkout_token
    resp = client.post(
        f"/api/v1/products/{product.ean}/purchase",
        json={"nfc_id": 999999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_purchase_insufficient_balance(client, checkout_token, product, test_user, db):
    token, _ = checkout_token
    test_user.balance = Decimal("0.50")  # price = 1.50
    db.commit()

    resp = client.post(
        f"/api/v1/products/{product.ean}/purchase",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402


def test_purchase_out_of_stock(client, checkout_token, product, test_user, db):
    token, _ = checkout_token
    product.stock = 0
    db.commit()

    resp = client.post(
        f"/api/v1/products/{product.ean}/purchase",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_purchase_inactive_product(client, checkout_token, inactive_product, test_user):
    token, _ = checkout_token
    resp = client.post(
        f"/api/v1/products/{inactive_product.ean}/purchase",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_purchase_requires_checkout_device(client, machine_token, product, test_user):
    """Regular machine token (type='machine') must be rejected."""
    token, _ = machine_token
    resp = client.post(
        f"/api/v1/products/{product.ean}/purchase",
        json={"nfc_id": test_user.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_purchase_requires_token(client, product, test_user):
    resp = client.post(
        f"/api/v1/products/{product.ean}/purchase",
        json={"nfc_id": test_user.id},
    )
    assert resp.status_code == 401
