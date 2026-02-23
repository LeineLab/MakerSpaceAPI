from datetime import UTC, datetime, timedelta

from authlib.jose import jwt
from authlib.jose.errors import JoseError

from app.config import settings

_ALGORITHM = "HS256"
_EXPIRE_HOURS = 8


def _key() -> bytes:
    """Return the signing key as bytes."""
    return settings.SECRET_KEY.encode()


def create_admin_jwt(user_info: dict) -> str:
    """Create a signed HS256 JWT from OIDC user_info. Expires in 8 hours."""
    payload = {
        **user_info,
        "exp": int((datetime.now(UTC) + timedelta(hours=_EXPIRE_HOURS)).timestamp()),
    }
    token_bytes = jwt.encode({"alg": _ALGORITHM}, payload, _key())
    return token_bytes.decode("utf-8") if isinstance(token_bytes, bytes) else token_bytes


def verify_admin_jwt(token: str | None) -> dict | None:
    """Verify a JWT and return the payload dict, or None if invalid/expired."""
    if not token:
        return None
    try:
        claims = jwt.decode(token, _key())
        claims.validate()
        return dict(claims)
    except (JoseError, Exception):
        return None
