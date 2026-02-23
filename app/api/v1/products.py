from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import (
    get_current_device,
    get_session_user,
    require_checkout_device,
    require_product_manager_user,
)
from app.auth.oidc import is_product_manager
from app.database import get_db
from app.models.machine import Machine
from app.models.product import Product, ProductAlias, ProductAudit, ProductAuditType, ProductCategory
from app.models.transaction import Transaction, TransactionType
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.product import (
    ProductAliasCreate,
    ProductAliasResponse,
    ProductAuditResponse,
    ProductCreate,
    ProductDetailResponse,
    ProductPopularityResponse,
    ProductResponse,
    ProductStockAdjust,
    ProductStocktaking,
    ProductUpdate,
)

router = APIRouter()


def _resolve_product(ean: str, db: Session) -> Product:
    """Find a product by EAN or alias EAN."""
    product = db.query(Product).filter(Product.ean == ean).first()
    if not product:
        alias = db.query(ProductAlias).filter(ProductAlias.ean == ean).first()
        if alias:
            product = db.query(Product).filter(Product.id == alias.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/products", response_model=list[ProductResponse])
def list_products(
    category: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: dict | None = Depends(get_session_user),
):
    """Public product list. Product managers may pass include_inactive=true to see all."""
    q = db.query(Product)
    if not (include_inactive and user and is_product_manager(user)):
        q = q.filter(Product.active.is_(True))
    if category:
        q = q.filter(Product.category == category)
    return q.order_by(Product.category, Product.name).all()


@router.get("/products.json", response_model=list[ProductResponse])
def list_products_json(
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Public JSON product feed (same as /products, active only)."""
    q = db.query(Product).filter(Product.active.is_(True))
    if category:
        q = q.filter(Product.category == category)
    return q.order_by(Product.category, Product.name).all()


@router.get("/categories", response_model=list[str])
def list_categories(db: Session = Depends(get_db)):
    """Return all product categories (ProductCategory table + active product categories), sorted."""
    from_table = {c.name for c in db.query(ProductCategory).all()}
    from_products = {
        r[0] for r in db.query(Product.category)
        .filter(Product.active.is_(True))
        .distinct()
        .all()
    }
    return sorted(from_table | from_products)


@router.post("/categories", response_model=str, status_code=201)
def create_category(
    body: dict,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    """Create a new product category (product manager only)."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name cannot be empty")
    if db.query(ProductCategory).filter(ProductCategory.name == name).first():
        raise HTTPException(status_code=409, detail="Category already exists")
    db.add(ProductCategory(name=name))
    db.commit()
    return name


@router.delete("/categories/{name}", response_model=MessageResponse)
def delete_category(
    name: str,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    """Delete a product category (product manager only)."""
    cat = db.query(ProductCategory).filter(ProductCategory.name == name).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    db.delete(cat)
    db.commit()
    return {"detail": f"Category '{name}' deleted"}


@router.get("/products/{ean}", response_model=ProductDetailResponse)
def get_product(ean: str, db: Session = Depends(get_db)):
    product = _resolve_product(ean, db)
    return product


@router.post("/products", response_model=ProductResponse, status_code=201)
def create_product(
    body: ProductCreate,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    if db.query(Product).filter(Product.ean == body.ean).first():
        raise HTTPException(status_code=409, detail="EAN already exists")
    product = Product(
        ean=body.ean,
        name=body.name,
        price=body.price,
        stock=body.stock,
        category=body.category,
    )
    db.add(product)
    db.flush()
    db.add(ProductAudit(
        product_id=product.id,
        changed_by=user.get("sub", "unknown"),
        change_type=ProductAuditType.created,
        new_value=body.ean,
    ))
    db.commit()
    db.refresh(product)
    return product


@router.put("/products/{ean}", response_model=ProductResponse)
def update_product(
    ean: str,
    body: ProductUpdate,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    product = _resolve_product(ean, db)
    actor = user.get("sub", "unknown")

    if body.name is not None and body.name != product.name:
        db.add(ProductAudit(
            product_id=product.id, changed_by=actor,
            change_type=ProductAuditType.name_change,
            old_value=product.name, new_value=body.name,
        ))
        product.name = body.name

    if body.price is not None and body.price != product.price:
        db.add(ProductAudit(
            product_id=product.id, changed_by=actor,
            change_type=ProductAuditType.price_change,
            old_value=str(product.price), new_value=str(body.price),
        ))
        product.price = body.price

    if body.category is not None and body.category != product.category:
        db.add(ProductAudit(
            product_id=product.id, changed_by=actor,
            change_type=ProductAuditType.category_change,
            old_value=product.category, new_value=body.category,
        ))
        product.category = body.category

    if body.active is not None and body.active != product.active:
        change_type = ProductAuditType.activated if body.active else ProductAuditType.deactivated
        db.add(ProductAudit(product_id=product.id, changed_by=actor, change_type=change_type))
        product.active = body.active

    db.commit()
    db.refresh(product)
    return product


@router.post("/products/{ean}/stock", response_model=ProductResponse)
def adjust_stock(
    ean: str,
    body: ProductStockAdjust,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    product = _resolve_product(ean, db)
    new_stock = product.stock + body.delta
    if new_stock < 0:
        raise HTTPException(status_code=400, detail="Stock cannot go below 0")
    change_type = ProductAuditType.stock_add if body.delta > 0 else ProductAuditType.stock_deduct
    db.add(ProductAudit(
        product_id=product.id,
        changed_by=user.get("sub", "unknown"),
        change_type=change_type,
        old_value=str(product.stock),
        new_value=str(new_stock),
        note=body.note,
    ))
    product.stock = new_stock
    db.commit()
    db.refresh(product)
    return product


@router.post("/products/{ean}/stocktaking", response_model=ProductResponse)
def stocktaking(
    ean: str,
    body: ProductStocktaking,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    """Set absolute stock count (stocktaking)."""
    if body.count < 0:
        raise HTTPException(status_code=400, detail="Count cannot be negative")
    product = _resolve_product(ean, db)
    db.add(ProductAudit(
        product_id=product.id,
        changed_by=user.get("sub", "unknown"),
        change_type=ProductAuditType.stocktaking,
        old_value=str(product.stock),
        new_value=str(body.count),
        note=body.note,
    ))
    product.stock = body.count
    db.commit()
    db.refresh(product)
    return product


@router.get("/products/{ean}/audit", response_model=list[ProductAuditResponse])
def get_product_audit(
    ean: str,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    product = _resolve_product(ean, db)
    return (
        db.query(ProductAudit)
        .filter(ProductAudit.product_id == product.id)
        .order_by(ProductAudit.changed_at.desc())
        .all()
    )


@router.get("/products/{ean}/popularity", response_model=ProductPopularityResponse)
def product_popularity(
    ean: str,
    days: int = Query(default=7, ge=1, le=365),
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    product = _resolve_product(ean, db)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    count = (
        db.query(func.count(Transaction.id))
        .filter(
            Transaction.product_id == product.id,
            Transaction.type == TransactionType.purchase,
            Transaction.created_at >= since,
        )
        .scalar()
    ) or 0
    return ProductPopularityResponse(
        product_id=product.id,
        ean=product.ean,
        name=product.name,
        purchase_count=count,
        days=days,
    )


# --- Aliases ---

@router.get("/products/{ean}/aliases", response_model=list[ProductAliasResponse])
def list_aliases(ean: str, db: Session = Depends(get_db)):
    product = _resolve_product(ean, db)
    return product.aliases


@router.post("/products/{ean}/aliases", response_model=ProductAliasResponse, status_code=201)
def add_alias(
    ean: str,
    body: ProductAliasCreate,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    product = _resolve_product(ean, db)
    if db.query(Product).filter(Product.ean == body.ean).first():
        raise HTTPException(status_code=409, detail="EAN already used as a primary product EAN")
    if db.query(ProductAlias).filter(ProductAlias.ean == body.ean).first():
        raise HTTPException(status_code=409, detail="Alias EAN already exists")
    alias = ProductAlias(ean=body.ean, product_id=product.id)
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return alias


@router.delete("/products/{ean}/aliases/{alias_ean}", response_model=MessageResponse)
def delete_alias(
    ean: str,
    alias_ean: str,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    product = _resolve_product(ean, db)
    alias = (
        db.query(ProductAlias)
        .filter(ProductAlias.ean == alias_ean, ProductAlias.product_id == product.id)
        .first()
    )
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    db.delete(alias)
    db.commit()
    return {"detail": "Alias removed"}


# --- Purchase (checkout device) ---

@router.post("/products/{ean}/purchase")
def purchase_product(
    ean: str,
    body: dict,
    device: Machine = Depends(require_checkout_device),
    db: Session = Depends(get_db),
):
    """Buy a product (checkout device only). Deducts price from user's balance."""
    nfc_id = body.get("nfc_id")
    if not nfc_id:
        raise HTTPException(status_code=400, detail="nfc_id required")

    product = _resolve_product(ean, db)
    if not product.active:
        raise HTTPException(status_code=400, detail="Product is not active")
    if product.stock <= 0:
        raise HTTPException(status_code=400, detail="Product out of stock")

    user = db.execute(
        select(User).where(User.id == nfc_id).with_for_update()
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.balance < product.price:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient balance. Price: {product.price}, balance: {user.balance}",
        )

    user.balance -= product.price
    product.stock -= 1

    db.add(Transaction(
        user_id=nfc_id,
        amount=-product.price,
        type=TransactionType.purchase,
        machine_id=device.id,
        product_id=product.id,
        note=product.name,
    ))
    db.commit()

    return {"detail": "Purchase successful", "product": product.name, "new_balance": float(user.balance)}
