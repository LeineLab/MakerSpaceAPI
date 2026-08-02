import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.jwt import create_admin_jwt, verify_admin_jwt, verify_link_token
from app.auth.oidc import is_admin, is_product_manager, oauth
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.web.i18n import detect_language, get_translator
from app.web.templating import templates as _templates

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


@router.get("/login")
async def login(request: Request):
    """Redirect to OIDC provider."""
    return await oauth.oidc.authorize_redirect(request, settings.OIDC_REDIRECT_URI)


@router.get("/callback")
async def callback(request: Request):
    """Handle OIDC callback, issue JWT cookie."""
    token = await oauth.oidc.authorize_access_token(request)
    user_info = token.get("userinfo")
    if not user_info:
        user_info = await oauth.oidc.userinfo(token=token)

    jwt_token = create_admin_jwt(dict(user_info))
    redirect_url = "/dashboard" if is_admin(dict(user_info)) else "/"
    response = RedirectResponse(url=redirect_url)
    response.set_cookie(
        key="auth_token",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        secure=not settings.DEBUG,
        max_age=8 * 3600,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/")
    response.delete_cookie("auth_token")
    request.session.pop("user", None)
    return response


@router.get("/connect/callback")
async def connect_callback(request: Request, db: Session = Depends(get_db)):
    """Handle OIDC callback for NFC self-service card linking."""
    locale = detect_language(request.headers.get("accept-language", ""))

    def _error(msg: str):
        return _templates.TemplateResponse(
            request, "connect_result.html",
            {"user": None, "flash": None,
             "_": get_translator(locale), "lang": locale,
             "success": False, "error": msg},
        )

    _ = get_translator(locale)

    link_token = request.session.pop("_link_token", None)
    nfc_id = verify_link_token(link_token)
    if nfc_id is None:
        return _error(_("connect.err_session"))

    try:
        token = await oauth.oidc.authorize_access_token(request)
    except Exception:
        return _error(_("connect.err_oidc"))

    user_info = token.get("userinfo")
    if not user_info:
        user_info = await oauth.oidc.userinfo(token=token)

    sub = user_info.get("sub")
    if not sub:
        return _error(_("connect.err_no_sub"))

    user = db.query(User).filter(User.id == nfc_id).first()
    if not user:
        return _error(_("connect.err_user_not_found"))
    if user.oidc_sub:
        return _error(_("connect.err_card_taken"))

    existing = db.query(User).filter(User.oidc_sub == sub).first()
    if existing:
        # Offer to transfer old card's data to the new card instead of failing
        request.session["_transfer"] = {
            "old_id": existing.id,
            "new_id": nfc_id,
            "sub": sub,
        }
        return _templates.TemplateResponse(
            request, "connect_transfer.html",
            {
                "user": None, "flash": None,
                "_": get_translator(locale), "lang": locale,
                "old_id": existing.id,
                "new_id": nfc_id,
                "old_name": existing.name,
                "old_balance": existing.balance,
            },
        )

    user.oidc_sub = sub
    if settings.OIDC_LINK_UPDATE_NAME:
        name = user_info.get("name")
        if name:
            user.name = name
    db.commit()

    display_name = user.name or user_info.get("name")
    return _templates.TemplateResponse(
        request, "connect_result.html",
        {"user": None, "flash": None,
         "_": get_translator(locale), "lang": locale,
         "success": True, "display_name": display_name},
    )


@router.get("/connect/{token}")
async def connect_start(token: str, request: Request):
    """Initiate OIDC login for NFC self-service card linking."""
    locale = detect_language(request.headers.get("accept-language", ""))
    nfc_id = verify_link_token(token)
    if nfc_id is None:
        return _templates.TemplateResponse(
            request, "connect_result.html",
            {"user": None, "flash": None,
             "_": get_translator(locale), "lang": locale,
             "success": False, "error": "This link is invalid or has expired. Please scan the QR code again."},
        )
    request.session["_link_token"] = token
    return await oauth.oidc.authorize_redirect(
        request, f"{settings.BASE_URL}/auth/connect/callback"
    )


@router.post("/connect/transfer")
async def connect_transfer(request: Request, db: Session = Depends(get_db)):
    """Execute the pending NFC card transfer after the user confirmed."""
    locale = detect_language(request.headers.get("accept-language", ""))
    _ = get_translator(locale)

    def _error(msg: str):
        return _templates.TemplateResponse(
            request, "connect_result.html",
            {"user": None, "flash": None,
             "_": _, "lang": locale,
             "success": False, "error": msg},
        )

    transfer_data = request.session.pop("_transfer", None)
    if not transfer_data:
        return _error(_("connect.err_session"))

    from app.api.v1.users import _do_transfer  # local import to avoid circular dependency
    try:
        user = _do_transfer(transfer_data["old_id"], transfer_data["new_id"], db)
    except HTTPException as e:
        db.rollback()
        return _error(e.detail)
    except Exception:
        db.rollback()
        logger.exception(
            "Card transfer failed (old_id=%s, new_id=%s)",
            transfer_data["old_id"], transfer_data["new_id"],
        )
        return _error(_("connect.err_transfer_failed"))

    return _templates.TemplateResponse(
        request, "connect_result.html",
        {"user": None, "flash": None,
         "_": _, "lang": locale,
         "success": True, "display_name": user.name if user else None},
    )


@router.get("/connect/transfer/cancel")
async def connect_transfer_cancel(request: Request):
    """Cancel a pending card transfer and return to the home page."""
    request.session.pop("_transfer", None)
    return RedirectResponse(url="/")


@router.get("/me")
async def me(request: Request):
    """Return current user info as JSON — used by Alpine.js for nav/guard."""
    token = request.cookies.get("auth_token")
    user = verify_admin_jwt(token)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({
        "authenticated": True,
        "sub": user.get("sub"),
        "name": user.get("name"),
        "is_admin": is_admin(user),
        "is_product_manager": is_product_manager(user),
    })
