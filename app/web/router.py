import pathlib
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.deps import get_session_user, require_admin_user, require_product_manager_user
from app.auth.oidc import is_admin, is_product_manager
from app.web.i18n import detect_language, get_translator
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
# Flash message helpers (kept for compatibility — can be removed later)
# ---------------------------------------------------------------------------

def _set_flash(request: Request, message: str, type: str = "success") -> None:
    request.session["_flash"] = {"message": message, "type": type}


def _pop_flash(request: Request) -> Optional[dict]:
    return request.session.pop("_flash", None)


def _ctx(request: Request, user: dict, **extra) -> dict:
    """Build a base template context dict including i18n translator."""
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
def product_list(request: Request, user: dict | None = Depends(get_session_user)):
    locale = detect_language(request.headers.get("accept-language", ""))
    return templates.TemplateResponse(
        "products/list.html",
        {
            "request": request,
            "user": user,
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
):
    return templates.TemplateResponse("dashboard.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Admin: Machines
# ---------------------------------------------------------------------------

@router.get("/machines", response_class=HTMLResponse)
def machines_list(
    request: Request,
    admin: dict = Depends(require_admin_user),
):
    return templates.TemplateResponse("machines/list.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Product manager: Products (manage)
# ---------------------------------------------------------------------------

@router.get("/products/manage", response_class=HTMLResponse)
def products_manage(
    request: Request,
    user: dict = Depends(require_product_manager_user),
):
    return templates.TemplateResponse("products/manage.html", _ctx(request, user))


# ---------------------------------------------------------------------------
# Admin: Bankomat / Booking Targets
# ---------------------------------------------------------------------------

@router.get("/bankomat", response_class=HTMLResponse)
def bankomat_targets(
    request: Request,
    admin: dict = Depends(require_admin_user),
):
    return templates.TemplateResponse("bankomat/targets.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Admin: Users
# ---------------------------------------------------------------------------

@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    admin: dict = Depends(require_admin_user),
):
    return templates.TemplateResponse("users/list.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Admin: Rentals
# ---------------------------------------------------------------------------

@router.get("/rentals", response_class=HTMLResponse)
def rentals_page(
    request: Request,
    admin: dict = Depends(require_admin_user),
):
    return templates.TemplateResponse("rentals/items.html", _ctx(request, admin))
