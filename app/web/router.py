from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.deps import get_session_user, require_admin_user, require_product_manager_user, require_session_user
from app.auth.oidc import is_admin, is_product_manager
from app.web.auth import router as auth_router
from app.web.i18n import detect_language, get_translator
from app.web.templating import templates

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
        request, "index.html", {
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
        request, "products/list.html",
        {
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
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Admin: Machines
# ---------------------------------------------------------------------------

@router.get("/machines", response_class=HTMLResponse)
def machines_list(
    request: Request,
    user: dict = Depends(require_session_user),
):
    return templates.TemplateResponse(
        request, "machines/list.html",
        _ctx(request, user, user_is_admin=is_admin(user)),
    )


@router.get("/machines/{slug}", response_class=HTMLResponse)
def machine_detail(
    slug: str,
    request: Request,
    user: dict = Depends(require_session_user),
):
    return templates.TemplateResponse(
        request, "machines/detail.html",
        _ctx(request, user, slug=slug, user_is_admin=is_admin(user)),
    )


# ---------------------------------------------------------------------------
# Product manager: Products (manage)
# ---------------------------------------------------------------------------

@router.get("/products/manage", response_class=HTMLResponse)
def products_manage(
    request: Request,
    user: dict = Depends(require_product_manager_user),
):
    return templates.TemplateResponse(request, "products/manage.html", _ctx(request, user))


# ---------------------------------------------------------------------------
# Admin: Bankomat / Booking Targets
# ---------------------------------------------------------------------------

@router.get("/bankomat", response_class=HTMLResponse)
def bankomat_targets(
    request: Request,
    admin: dict = Depends(require_admin_user),
):
    return templates.TemplateResponse(request, "bankomat/targets.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Admin: Users
# ---------------------------------------------------------------------------

@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    admin: dict = Depends(require_admin_user),
):
    return templates.TemplateResponse(request, "users/list.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Admin: Rentals
# ---------------------------------------------------------------------------

@router.get("/rentals", response_class=HTMLResponse)
def rentals_page(
    request: Request,
    admin: dict = Depends(require_admin_user),
):
    return templates.TemplateResponse(request, "rentals/items.html", _ctx(request, admin))


# ---------------------------------------------------------------------------
# Self-service: My Account
# ---------------------------------------------------------------------------

@router.get("/me", response_class=HTMLResponse)
def me_page(
    request: Request,
    user: dict = Depends(require_session_user),
):
    return templates.TemplateResponse(request, "users/me.html", _ctx(request, user))
