from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.auth.oidc import oauth
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    """Redirect to OIDC provider."""
    return await oauth.oidc.authorize_redirect(request, settings.OIDC_REDIRECT_URI)


@router.get("/callback")
async def callback(request: Request):
    """Handle OIDC callback, store user info in session."""
    token = await oauth.oidc.authorize_access_token(request)
    user_info = token.get("userinfo")
    if not user_info:
        user_info = await oauth.oidc.userinfo(token=token)
    request.session["user"] = dict(user_info)
    return RedirectResponse(url="/dashboard")


@router.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/")
