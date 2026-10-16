"""Post-login deep link: `/auth/login?next=/path` is kept in the server-side
session and used by the callback, so the redirect URI registered at the OIDC
provider stays one fixed URL."""
from types import SimpleNamespace

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from app.main import app

from app.web import auth as web_auth


@pytest.fixture
def https_client():
    """The session cookie is `Secure` outside DEBUG, so it is only sent back
    over https — the plain http `client` fixture would silently drop it."""
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def fake_oidc(monkeypatch):
    async def authorize_redirect(request, redirect_uri):
        return RedirectResponse("https://idp.example/authorize")

    async def authorize_access_token(request):
        return {"userinfo": {"sub": "u1", "name": "U", "groups": []}}

    monkeypatch.setattr(
        web_auth, "oauth",
        SimpleNamespace(oidc=SimpleNamespace(
            authorize_redirect=authorize_redirect, authorize_access_token=authorize_access_token,
        )),
    )


def _login_then_callback(client, next_value=None):
    params = {"next": next_value} if next_value is not None else {}
    client.get("/auth/login", params=params, follow_redirects=False)
    return client.get("/auth/callback", follow_redirects=False)


def test_login_without_next_goes_to_default(https_client, fake_oidc):
    resp = _login_then_callback(https_client)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/"


def test_login_with_next_returns_to_path_and_query(https_client, fake_oidc):
    resp = _login_then_callback(https_client, "/ledger/buchungen?jahr=2025&seite=2")
    assert resp.headers["location"] == "/ledger/buchungen?jahr=2025&seite=2"


@pytest.mark.parametrize("bad", [
    "https://evil.example/", "//evil.example/", "/\\evil.example", "evil", "/auth/logout", "/a\nb",
])
def test_login_rejects_unsafe_next(https_client, fake_oidc, bad):
    resp = _login_then_callback(https_client, bad)
    assert resp.headers["location"] == "/"


def test_next_is_used_only_once(https_client, fake_oidc):
    _login_then_callback(https_client, "/ledger")
    resp = _login_then_callback(https_client)
    assert resp.headers["location"] == "/"


def test_login_required_page_links_back(client):
    resp = client.get("/ledger/kassenpruefung")
    assert resp.status_code == 401
    assert 'href="/auth/login?next=%2Fledger%2Fkassenpruefung"' in resp.text
