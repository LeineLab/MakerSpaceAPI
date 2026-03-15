from fastapi import APIRouter, Depends, Request
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
        return _error(_("connect.err_oidc_taken"))

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
