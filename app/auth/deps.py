from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.jwt import verify_admin_jwt
from app.auth.oidc import is_admin, is_machine_admin, is_product_manager
from app.auth.tokens import verify_api_token
from app.database import get_db
from app.models.machine import Machine

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# OIDC / JWT helpers
# ---------------------------------------------------------------------------

def get_session_user(request: Request) -> dict | None:
    """Return user from JWT (Authorization: Bearer header or auth_token cookie)."""
    # 1. Authorization: Bearer <jwt>
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        user = verify_admin_jwt(auth_header[7:])
        if user:
            return user
    # 2. httpOnly auth_token cookie
    return verify_admin_jwt(request.cookies.get("auth_token"))


def require_session_user(request: Request) -> dict:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin_user(user: dict = Depends(require_session_user)) -> dict:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_product_manager_user(user: dict = Depends(require_session_user)) -> dict:
    """Allow global admins and users in the product-manager OIDC group."""
    if not is_product_manager(user):
        raise HTTPException(status_code=403, detail="Product manager access required")
    return user


# ---------------------------------------------------------------------------
# Device API token auth
# ---------------------------------------------------------------------------

def get_current_device(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Machine:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing API token")
    token = credentials.credentials
    # Linear scan is fine — machines table is small and query hits the hash index
    machine = (
        db.query(Machine)
        .filter(Machine.active.is_(True))
        .all()
    )
    for m in machine:
        if verify_api_token(token, m.api_token_hash):
            m.last_active_at = datetime.now(UTC).replace(tzinfo=None)
            db.commit()
            db.refresh(m)
            return m
    raise HTTPException(status_code=401, detail="Invalid or revoked API token")


def require_checkout_device(
    device: Machine = Depends(get_current_device),
) -> Machine:
    """Restrict endpoint to checkout-type devices."""
    from app.config import settings

    if device.slug not in settings.checkout_box_slug_list and device.machine_type != "checkout":
        raise HTTPException(status_code=403, detail="Endpoint restricted to checkout devices")
    return device


# ---------------------------------------------------------------------------
# Combined: device token OR admin session
# ---------------------------------------------------------------------------

def require_device_or_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Machine | dict:
    """Accept either an active device API token or an admin OIDC session.

    Returns the Machine object when authenticated via device token, or the
    admin user dict when authenticated via OIDC session.
    """
    if credentials is not None:
        token = credentials.credentials
        # Try as machine API token first (verify_admin_jwt returns None on non-JWT input)
        for m in db.query(Machine).filter(Machine.active.is_(True)).all():
            if verify_api_token(token, m.api_token_hash):
                m.last_active_at = datetime.now(UTC).replace(tzinfo=None)
                db.commit()
                db.refresh(m)
                return m
    # Try admin session (cookie or Bearer JWT)
    user = get_session_user(request)
    if user and is_admin(user):
        return user
    raise HTTPException(status_code=401, detail="Device token or admin session required")


# ---------------------------------------------------------------------------
# Combined: admin OR machine sub-admin for a specific machine
# ---------------------------------------------------------------------------

def require_machine_manager(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
) -> tuple[dict, Machine]:
    """Return (user_info, machine) if user is global admin or sub-admin for this machine."""
    user = require_session_user(request)
    machine = db.query(Machine).filter(Machine.slug == slug, Machine.active.is_(True)).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    admin_subs = [a.oidc_sub for a in machine.admin_users]
    if not (is_admin(user) or is_machine_admin(user, admin_subs)):
        raise HTTPException(status_code=403, detail="Not authorized for this machine")
    return user, machine
