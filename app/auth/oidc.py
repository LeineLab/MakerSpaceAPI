from authlib.integrations.starlette_client import OAuth

from app.config import settings

oauth = OAuth()

oauth.register(
    name="oidc",
    client_id=settings.OIDC_CLIENT_ID,
    client_secret=settings.OIDC_CLIENT_SECRET,
    server_metadata_url=settings.OIDC_DISCOVERY_URL,
    client_kwargs={
        "scope": "openid email profile",
        "code_challenge_method": "S256",
    },
)


def get_user_groups(user_info: dict) -> list[str]:
    """Extract group list from OIDC claims using the configured claim name."""
    groups = user_info.get(settings.OIDC_GROUP_CLAIM, [])
    if isinstance(groups, str):
        return [groups]
    return list(groups)


def is_admin(user_info: dict) -> bool:
    return settings.OIDC_ADMIN_GROUP in get_user_groups(user_info)


def is_machine_admin(user_info: dict, oidc_groups: list[str]) -> bool:
    """Check whether user belongs to any of the machine's admin groups."""
    user_groups = set(get_user_groups(user_info))
    return bool(user_groups.intersection(set(oidc_groups)))
