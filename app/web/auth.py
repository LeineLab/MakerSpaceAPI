from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.jwt import create_admin_jwt, verify_admin_jwt
from app.auth.oidc import is_admin, is_product_manager, oauth
from app.config import settings

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
    response = RedirectResponse(url="/dashboard")
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
