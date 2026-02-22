import hashlib
import secrets


def generate_api_token() -> tuple[str, str]:
    """Generate a new API token.

    Returns (plaintext_token, sha256_hash). Store the hash; return the
    plaintext to the admin once.
    """
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    return token, token_hash


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_api_token(token: str, stored_hash: str) -> bool:
    """Constant-time comparison of token against its stored SHA-256 hash."""
    computed = _hash_token(token)
    return secrets.compare_digest(computed, stored_hash)
