from datetime import UTC, datetime, timedelta

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey
from joserfc.jwt import JWTClaimsRegistry

from app.config import settings

_ALGORITHM = "HS256"
_EXPIRE_HOURS = 8
_claims_registry = JWTClaimsRegistry()


def _key() -> OctKey:
    """Return the signing key."""
    return OctKey.import_key(settings.SECRET_KEY.encode())


def create_admin_jwt(user_info: dict) -> str:
    """Create a signed HS256 JWT from OIDC user_info. Expires in 8 hours."""
    payload = {
        **user_info,
        "exp": int((datetime.now(UTC) + timedelta(hours=_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode({"alg": _ALGORITHM}, payload, _key())


def verify_admin_jwt(token: str | None) -> dict | None:
    """Verify a JWT and return the payload dict, or None if invalid/expired."""
    if not token:
        return None
    try:
        decoded = jwt.decode(token, _key())
        _claims_registry.validate(decoded.claims)
        return dict(decoded.claims)
    except (JoseError, Exception):
        return None


LINK_TOKEN_TTL = 900  # 15 minutes


def create_link_token(nfc_id: int) -> str:
    """Create a signed HS256 JWT for NFC-to-OIDC self-service linking. Expires in 15 minutes."""
    payload = {
        "type": "nfc_link",
        "nfc_id": nfc_id,
        "exp": int((datetime.now(UTC) + timedelta(seconds=LINK_TOKEN_TTL)).timestamp()),
    }
    return jwt.encode({"alg": _ALGORITHM}, payload, _key())


def verify_link_token(token: str | None) -> int | None:
    """Verify a link token and return the nfc_id, or None if invalid/expired/wrong type."""
    if not token:
        return None
    try:
        decoded = jwt.decode(token, _key())
        _claims_registry.validate(decoded.claims)
        if decoded.claims.get("type") != "nfc_link":
            return None
        return int(decoded.claims["nfc_id"])
    except (JoseError, Exception):
        return None
