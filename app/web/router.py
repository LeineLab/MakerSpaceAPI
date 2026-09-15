from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth.deps import (
    get_session_user,
    require_admin_user,
    require_ledger_viewer_user,
    require_product_manager_user,
    require_session_user,
)
from app.auth.oidc import is_admin, is_auditor_writer, is_product_manager, is_treasurer
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
# Treasurer / Auditor: Ledger (Vereinsbuchhaltung)
# ---------------------------------------------------------------------------

# Deep-linkable tab URLs (e.g. /ledger/kassenpruefung) — previously every
# sub-section only lived behind client-side Alpine state (activeTab) with no
# URL of its own, so there was no way to bookmark or share a link straight
# to one. ASCII slugs (no umlauts), same transliteration convention already
# used for category/machine slugs elsewhere in this app (e.g.
# "mitgliedsbeitraege"). Keys are the tab identifiers used by the frontend's
# own `activeTab`/`switchTab()` — kept in sync by hand with the matching
# LEDGER_TAB_SLUGS JS object in ledger/index.html (no shared implementation
# is possible across Python/JS, same convention as #51/#52's date-range
# mirroring).
LEDGER_TAB_SLUGS = {
    "buchungen": "entries",
    "belege": "belege",
    "kategorien": "categories",
    "import": "import",
    "kassen": "targets",
    "bankkonten": "accounts",
    "anlagevermoegen": "assets",
    "ruecklagen": "reserves",
    "kassenpruefung": "audit-reports",
    "euer-bericht": "report",
}


def _render_ledger_page(request: Request, user: dict, initial_tab: str):
    return templates.TemplateResponse(
        request, "ledger/index.html",
        _ctx(
            request, user, user_is_treasurer=is_treasurer(user), user_can_write_audit_reports=is_auditor_writer(user),
            initial_tab=initial_tab,
        ),
    )


@router.get("/ledger", response_class=HTMLResponse)
def ledger_page(
    request: Request,
    user: dict = Depends(require_ledger_viewer_user),
):
    return _render_ledger_page(request, user, initial_tab="entries")


@router.get("/ledger/{tab_slug}", response_class=HTMLResponse)
def ledger_tab_page(
    tab_slug: str,
    request: Request,
    user: dict = Depends(require_ledger_viewer_user),
):
    initial_tab = LEDGER_TAB_SLUGS.get(tab_slug)
    if initial_tab is None:
        raise HTTPException(status_code=404, detail="Unknown ledger tab")
    return _render_ledger_page(request, user, initial_tab)


# ---------------------------------------------------------------------------
# Self-service: My Account
# ---------------------------------------------------------------------------

@router.get("/me", response_class=HTMLResponse)
def me_page(
    request: Request,
    user: dict = Depends(require_session_user),
):
    return templates.TemplateResponse(request, "users/me.html", _ctx(request, user))
