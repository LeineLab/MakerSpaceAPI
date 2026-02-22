from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.deps import get_session_user, require_admin_user, require_session_user
from app.database import get_db
from app.models.booking_target import BookingTarget
from app.models.machine import Machine, MachineAuthorization
from app.models.product import Product
from app.models.rental import Rental, RentalItem, RentalPermission
from app.models.user import User
from app.web.auth import router as auth_router

import pathlib

_templates_dir = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

router = APIRouter()
router.include_router(auth_router)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, user: dict | None = Depends(get_session_user)):
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@router.get("/products", response_class=HTMLResponse)
def product_list(
    request: Request,
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Public product list page."""
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
    return templates.TemplateResponse(
        "products/list.html",
        {
            "request": request,
            "products": products,
            "categories": [c[0] for c in categories],
            "selected_category": category,
            "user": get_session_user(request),
        },
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    user_count = db.query(User).count()
    machine_count = db.query(Machine).filter(Machine.active.is_(True)).count()
    product_count = db.query(Product).filter(Product.active.is_(True)).count()
    target_count = db.query(BookingTarget).count()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": admin,
            "stats": {
                "users": user_count,
                "machines": machine_count,
                "products": product_count,
                "targets": target_count,
            },
        },
    )


@router.get("/machines", response_class=HTMLResponse)
def machines_list(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    machines = db.query(Machine).order_by(Machine.name).all()
    return templates.TemplateResponse(
        "machines/list.html",
        {"request": request, "user": admin, "machines": machines},
    )


@router.get("/products/manage", response_class=HTMLResponse)
def products_manage(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    products = db.query(Product).order_by(Product.category, Product.name).all()
    categories = (
        db.query(Product.category).distinct().order_by(Product.category).all()
    )
    return templates.TemplateResponse(
        "products/manage.html",
        {
            "request": request,
            "user": admin,
            "products": products,
            "categories": [c[0] for c in categories],
        },
    )


@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(
        "users/list.html",
        {"request": request, "user": admin, "users": users},
    )


@router.get("/bankomat", response_class=HTMLResponse)
def bankomat_targets(
    request: Request,
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    targets = db.query(BookingTarget).order_by(BookingTarget.name).all()
    return templates.TemplateResponse(
        "bankomat/targets.html",
        {"request": request, "user": admin, "targets": targets},
    )


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
        {
            "request": request,
            "user": admin,
            "items": items,
            "active_rentals": active_rentals,
            "permissions": permissions,
        },
    )
