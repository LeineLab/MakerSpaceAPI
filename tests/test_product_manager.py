"""Tests for the product-manager OIDC role."""
from unittest.mock import patch

from app.auth.oidc import is_product_manager


# ---------------------------------------------------------------------------
# is_product_manager helper
# ---------------------------------------------------------------------------

def test_admin_is_also_product_manager():
    with patch("app.auth.oidc.settings") as mock_settings:
        mock_settings.OIDC_ADMIN_GROUP = "admins"
        mock_settings.OIDC_PRODUCT_MANAGER_GROUP = "product-managers"
        mock_settings.OIDC_GROUP_CLAIM = "groups"
        user = {"groups": ["admins"]}
        assert is_product_manager(user) is True


def test_product_manager_group_member():
    with patch("app.auth.oidc.settings") as mock_settings:
        mock_settings.OIDC_ADMIN_GROUP = "admins"
        mock_settings.OIDC_PRODUCT_MANAGER_GROUP = "product-managers"
        mock_settings.OIDC_GROUP_CLAIM = "groups"
        user = {"groups": ["product-managers"]}
        assert is_product_manager(user) is True


def test_regular_user_is_not_product_manager():
    with patch("app.auth.oidc.settings") as mock_settings:
        mock_settings.OIDC_ADMIN_GROUP = "admins"
        mock_settings.OIDC_PRODUCT_MANAGER_GROUP = "product-managers"
        mock_settings.OIDC_GROUP_CLAIM = "groups"
        user = {"groups": ["other-group"]}
        assert is_product_manager(user) is False


def test_product_manager_disabled_when_group_empty():
    """When OIDC_PRODUCT_MANAGER_GROUP is empty, only admins pass."""
    with patch("app.auth.oidc.settings") as mock_settings:
        mock_settings.OIDC_ADMIN_GROUP = "admins"
        mock_settings.OIDC_PRODUCT_MANAGER_GROUP = ""
        mock_settings.OIDC_GROUP_CLAIM = "groups"
        user = {"groups": ["product-managers"]}
        assert is_product_manager(user) is False


# ---------------------------------------------------------------------------
# Web route access control
# ---------------------------------------------------------------------------

def test_product_manager_can_access_manage_page(product_manager_client):
    resp = product_manager_client.get("/products/manage")
    assert resp.status_code == 200


def test_product_manager_cannot_access_dashboard(product_manager_client):
    resp = product_manager_client.get("/dashboard")
    # redirected to login (302/303) or forbidden (403)
    assert resp.status_code in (302, 303, 401, 403)


def test_product_manager_cannot_access_machines(product_manager_client):
    resp = product_manager_client.get("/machines")
    assert resp.status_code in (302, 303, 401, 403)


def test_product_manager_cannot_access_users(product_manager_client):
    resp = product_manager_client.get("/users")
    assert resp.status_code in (302, 303, 401, 403)


def test_product_manager_cannot_access_bankomat(product_manager_client):
    resp = product_manager_client.get("/bankomat")
    assert resp.status_code in (302, 303, 401, 403)


def test_product_manager_cannot_access_rentals(product_manager_client):
    resp = product_manager_client.get("/rentals")
    assert resp.status_code in (302, 303, 401, 403)


def test_admin_can_still_access_manage_page(admin_client):
    resp = admin_client.get("/products/manage")
    assert resp.status_code == 200
