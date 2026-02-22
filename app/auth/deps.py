from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.oidc import is_admin, is_machine_admin, is_product_manager
from app.auth.tokens import verify_api_token
from app.database import get_db
from app.models.machine import Machine

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# OIDC / web session helpers
# ---------------------------------------------------------------------------

def get_session_user(request: Request) -> dict | None:
    """Return OIDC user dict from session, or None if not logged in."""
    return request.session.get("user")


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

    admin_group_names = [g.oidc_group for g in machine.admin_groups]
    if not (is_admin(user) or is_machine_admin(user, admin_group_names)):
        raise HTTPException(status_code=403, detail="Not authorized for this machine")
    return user, machine
