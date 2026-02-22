from datetime import UTC, datetime
from decimal import Decimal
from typing import Optional

import pathlib

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.deps import get_session_user, require_admin_user, require_product_manager_user
from app.auth.oidc import is_admin, is_product_manager
from app.auth.tokens import generate_api_token
from app.web.i18n import detect_language, get_translator
from app.database import get_db
from app.models.booking_target import BookingTarget
from app.models.machine import Machine, MachineAuthorization
from app.models.product import Product, ProductAlias, ProductAudit, ProductAuditType, ProductCategory
from app.models.rental import Rental, RentalItem, RentalPermission
from app.models.user import User
from app.web.auth import router as auth_router

_templates_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))
templates.env.globals["is_admin"] = lambda u: is_admin(u) if u else False
templates.env.globals["is_product_manager"] = lambda u: is_product_manager(u) if u else False
# Default translator (English) — overridden per-request via template context
templates.env.globals["_"] = get_translator("en")

router = APIRouter()
router.include_router(auth_router)


# ---------------------------------------------------------------------------
# Flash message helpers (stored in session, consumed once)
# ---------------------------------------------------------------------------

def _set_flash(request: Request, message: str, type: str = "success") -> None:
    request.session["_flash"] = {"message": message, "type": type}


def _pop_flash(request: Request) -> Optional[dict]:
    return request.session.pop("_flash", None)


def _ctx(request: Request, user: dict, db: Session, **extra) -> dict:
    """Build a base template context dict including flash and i18n translator."""
    locale = detect_language(request.headers.get("accept-language", ""))
    return {
        "request": request,
        "user": user,
        "flash": _pop_flash(request),
        "_": get_translator(locale),
        "lang": locale,
        **extra,
    }


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def index(request: Request, user: dict | None = Depends(get_session_user)):
    locale = detect_language(request.headers.get("accept-language", ""))
    return templates.TemplateResponse(
        "index.html", {
            "request": request,
            "user": user,
            "flash": _pop_flash(request),
            "_": get_translator(locale),
            "lang": locale,
        }
    )


@router.get("/products", response_class=HTMLResponse)
def product_list(
    request: Request,
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(Product).filter(Product.active.is_(True))
    if category:
        q = q.filter(Product.category == category)
    products = q.order_by(Product.category, Product.name).all()
    categories = (
        db.query(Product.category)
        .filter(Product.active.is_(True))
        .distinct()
        .order_by(Product.category)
        .all()
    )
    locale = detect_language(request.headers.get("accept-language", ""))
    return templates.TemplateResponse(
        "products/list.html",
        {
            "request": request,
            "products": products,
            "categories": [c[0] for c in categories],
            "selected_category": category,
            "user": get_session_user(request),
            "flash": _pop_flash(request),
            "_": get_translator(locale),
            "lang": locale,
        },
    )


# ---------------------------------------------------------------------------
# Admin: Dashboard
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        "dashboard.html",
        _ctx(request, admin, db, stats={
            "users":    db.query(User).count(),
            "machines": db.query(Machine).filter(Machine.active.is_(True)).count(),
            "products": db.query(Product).filter(Product.active.is_(True)).count(),
            "targets":  db.query(BookingTarget).count(),
        }),
    )


# ---------------------------------------------------------------------------
# Admin: Machines
# ---------------------------------------------------------------------------

@router.get("/machines", response_class=HTMLResponse)
def machines_list(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machines = db.query(Machine).order_by(Machine.name).all()
    # pending token is shown once after registration, stored separately so it
    # can be styled differently from normal flash messages
    pending_token = request.session.pop("_pending_token", None)
    return templates.TemplateResponse(
        "machines/list.html",
        _ctx(request, admin, db, machines=machines, pending_token=pending_token),
    )


@router.post("/machines/new")
def machines_create(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    slug: str = Form(...),
    machine_type: str = Form(...),
):
    slug = slug.strip().lower()
    if db.query(Machine).filter(Machine.slug == slug).first():
        _set_flash(request, f"Slug '{slug}' is already in use.", "error")
        return RedirectResponse("/machines", status_code=303)

    plaintext_token, token_hash = generate_api_token()
    machine = Machine(
        name=name.strip(),
        slug=slug,
        machine_type=machine_type,
        api_token_hash=token_hash,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        created_by=admin.get("sub"),
        active=True,
    )
    db.add(machine)
    db.commit()

    # Store the token separately so the template can display it prominently
    request.session["_pending_token"] = {
        "machine": machine.name,
        "slug": machine.slug,
        "token": plaintext_token,
    }
    return RedirectResponse("/machines", status_code=303)


# ---------------------------------------------------------------------------
# Product manager: Products (manage) — accessible by admins and product managers
# ---------------------------------------------------------------------------

@router.get("/products/manage", response_class=HTMLResponse)
def products_manage(
    request: Request,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    products = db.query(Product).order_by(Product.category, Product.name).all()
    categories = db.query(ProductCategory).order_by(ProductCategory.name).all()
    aliases = db.query(ProductAlias).order_by(ProductAlias.product_id, ProductAlias.ean).all()
    return templates.TemplateResponse(
        "products/manage.html",
        _ctx(request, user, db, products=products, categories=categories, aliases=aliases),
    )


@router.post("/products/manage/categories/new")
def products_category_create(
    request: Request,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
):
    name = name.strip()
    if not name:
        _set_flash(request, "Category name cannot be empty.", "error")
        return RedirectResponse("/products/manage", status_code=303)
    if db.query(ProductCategory).filter(ProductCategory.name == name).first():
        _set_flash(request, f"Category '{name}' already exists.", "error")
        return RedirectResponse("/products/manage", status_code=303)
    db.add(ProductCategory(name=name))
    db.commit()
    _set_flash(request, f"Category '{name}' added.")
    return RedirectResponse("/products/manage", status_code=303)


@router.post("/products/manage/{ean}/aliases/new")
def products_alias_create(
    ean: str,
    request: Request,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
    alias_ean: str = Form(...),
):
    product = db.query(Product).filter(Product.ean == ean).first()
    if not product:
        _set_flash(request, "Product not found.", "error")
        return RedirectResponse("/products/manage", status_code=303)
    alias_ean = alias_ean.strip()
    if db.query(Product).filter(Product.ean == alias_ean).first():
        _set_flash(request, f"EAN '{alias_ean}' is already a primary product EAN.", "error")
        return RedirectResponse("/products/manage", status_code=303)
    if db.query(ProductAlias).filter(ProductAlias.ean == alias_ean).first():
        _set_flash(request, f"EAN '{alias_ean}' is already registered as an alias.", "error")
        return RedirectResponse("/products/manage", status_code=303)
    db.add(ProductAlias(ean=alias_ean, product_id=product.id))
    db.commit()
    _set_flash(request, f"Alias '{alias_ean}' added to '{product.name}'.")
    return RedirectResponse("/products/manage", status_code=303)


@router.post("/products/manage/{ean}/aliases/{alias_ean}/delete")
def products_alias_delete(
    ean: str,
    alias_ean: str,
    request: Request,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
):
    product = db.query(Product).filter(Product.ean == ean).first()
    if not product:
        _set_flash(request, "Product not found.", "error")
        return RedirectResponse("/products/manage", status_code=303)
    alias = (
        db.query(ProductAlias)
        .filter(ProductAlias.ean == alias_ean, ProductAlias.product_id == product.id)
        .first()
    )
    if not alias:
        _set_flash(request, "Alias not found.", "error")
        return RedirectResponse("/products/manage", status_code=303)
    db.delete(alias)
    db.commit()
    _set_flash(request, f"Alias '{alias_ean}' removed.")
    return RedirectResponse("/products/manage", status_code=303)


@router.post("/products/manage/new")
def products_create(
    request: Request,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
    ean: str = Form(...),
    name: str = Form(...),
    price: str = Form(...),
    stock: int = Form(0),
    category: str = Form(...),
    active: Optional[str] = Form(None),
):
    ean = ean.strip()
    if db.query(Product).filter(Product.ean == ean).first():
        _set_flash(request, f"EAN '{ean}' already exists.", "error")
        return RedirectResponse("/products/manage", status_code=303)

    try:
        price_dec = Decimal(price)
    except Exception:
        _set_flash(request, "Invalid price.", "error")
        return RedirectResponse("/products/manage", status_code=303)

    product = Product(
        ean=ean,
        name=name.strip(),
        price=price_dec,
        stock=stock,
        category=category.strip(),
        active=(active == "on"),
    )
    db.add(product)
    db.flush()
    db.add(ProductAudit(
        product_id=product.id,
        changed_by=user.get("sub", "unknown"),
        change_type=ProductAuditType.created,
        new_value=ean,
    ))
    db.commit()
    _set_flash(request, f"Product '{name}' created.")
    return RedirectResponse("/products/manage", status_code=303)


@router.post("/products/manage/{ean}/edit")
def products_edit(
    ean: str,
    request: Request,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    price: str = Form(...),
    category: str = Form(...),
    active: Optional[str] = Form(None),
):
    product = db.query(Product).filter(Product.ean == ean).first()
    if not product:
        _set_flash(request, "Product not found.", "error")
        return RedirectResponse("/products/manage", status_code=303)

    actor = user.get("sub", "unknown")
    new_name = name.strip()
    new_category = category.strip()
    new_active = (active == "on")

    try:
        new_price = Decimal(price)
    except Exception:
        _set_flash(request, "Invalid price.", "error")
        return RedirectResponse("/products/manage", status_code=303)

    if new_name != product.name:
        db.add(ProductAudit(product_id=product.id, changed_by=actor,
                            change_type=ProductAuditType.name_change,
                            old_value=product.name, new_value=new_name))
        product.name = new_name
    if new_price != product.price:
        db.add(ProductAudit(product_id=product.id, changed_by=actor,
                            change_type=ProductAuditType.price_change,
                            old_value=str(product.price), new_value=str(new_price)))
        product.price = new_price
    if new_category != product.category:
        db.add(ProductAudit(product_id=product.id, changed_by=actor,
                            change_type=ProductAuditType.category_change,
                            old_value=product.category, new_value=new_category))
        product.category = new_category
    if new_active != product.active:
        change_type = ProductAuditType.activated if new_active else ProductAuditType.deactivated
        db.add(ProductAudit(product_id=product.id, changed_by=actor, change_type=change_type))
        product.active = new_active

    db.commit()
    _set_flash(request, f"Product '{product.name}' updated.")
    return RedirectResponse("/products/manage", status_code=303)


@router.post("/products/manage/{ean}/stock")
def products_stock(
    ean: str,
    request: Request,
    user: dict = Depends(require_product_manager_user),
    db: Session = Depends(get_db),
    delta: int = Form(...),
    note: Optional[str] = Form(None),
):
    product = db.query(Product).filter(Product.ean == ean).first()
    if not product:
        _set_flash(request, "Product not found.", "error")
        return RedirectResponse("/products/manage", status_code=303)

    new_stock = product.stock + delta
    if new_stock < 0:
        _set_flash(request, "Stock cannot go below 0.", "error")
        return RedirectResponse("/products/manage", status_code=303)

    change_type = ProductAuditType.stock_add if delta > 0 else ProductAuditType.stock_deduct
    db.add(ProductAudit(
        product_id=product.id,
        changed_by=user.get("sub", "unknown"),
        change_type=change_type,
        old_value=str(product.stock),
        new_value=str(new_stock),
        note=note or None,
    ))
    product.stock = new_stock
    db.commit()
    _set_flash(request, f"Stock updated to {new_stock}.")
    return RedirectResponse("/products/manage", status_code=303)


# ---------------------------------------------------------------------------
# Admin: Bankomat / Booking Targets
# ---------------------------------------------------------------------------

@router.get("/bankomat", response_class=HTMLResponse)
def bankomat_targets(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    targets = db.query(BookingTarget).order_by(BookingTarget.name).all()
    return templates.TemplateResponse(
        "bankomat/targets.html",
        _ctx(request, admin, db, targets=targets),
    )


@router.post("/bankomat/targets/new")
def bankomat_target_create(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    slug: str = Form(...),
):
    slug = slug.strip().lower()
    if db.query(BookingTarget).filter(BookingTarget.slug == slug).first():
        _set_flash(request, f"Slug '{slug}' is already in use.", "error")
        return RedirectResponse("/bankomat", status_code=303)
    if db.query(BookingTarget).filter(BookingTarget.name == name.strip()).first():
        _set_flash(request, f"Name '{name}' is already in use.", "error")
        return RedirectResponse("/bankomat", status_code=303)

    db.add(BookingTarget(
        name=name.strip(),
        slug=slug,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    db.commit()
    _set_flash(request, f"Booking target '{name}' created.")
    return RedirectResponse("/bankomat", status_code=303)


# ---------------------------------------------------------------------------
# Admin: Users
# ---------------------------------------------------------------------------

@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(
        "users/list.html",
        _ctx(request, admin, db, users=users),
    )


# ---------------------------------------------------------------------------
# Admin: Rentals
# ---------------------------------------------------------------------------

@router.get("/rentals", response_class=HTMLResponse)
def rentals_page(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    items = db.query(RentalItem).order_by(RentalItem.name).all()
    active_rentals = (
        db.query(Rental).filter(Rental.returned_at.is_(None)).order_by(Rental.rented_at).all()
    )
    permissions = db.query(RentalPermission).all()
    return templates.TemplateResponse(
        "rentals/items.html",
        _ctx(request, admin, db, items=items,
             active_rentals=active_rentals, permissions=permissions),
    )


@router.post("/rentals/items/new")
def rentals_item_create(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
    name: str = Form(...),
    uhf_tid: str = Form(...),
    description: Optional[str] = Form(None),
):
    tid = uhf_tid.strip().upper()
    if db.query(RentalItem).filter(RentalItem.uhf_tid == tid).first():
        _set_flash(request, f"UHF TID '{tid}' is already registered.", "error")
        return RedirectResponse("/rentals", status_code=303)

    db.add(RentalItem(
        name=name.strip(),
        uhf_tid=tid,
        description=description.strip() if description and description.strip() else None,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    db.commit()
    _set_flash(request, f"Rental item '{name}' added.")
    return RedirectResponse("/rentals", status_code=303)
