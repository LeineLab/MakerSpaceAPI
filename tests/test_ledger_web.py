"""Deep-linkable ledger tab URLs (/ledger/<slug>) — previously every
sub-section only lived behind client-side Alpine state with no URL of its
own. See CLAUDE.md Key Design Decision #53 and LEDGER_TAB_SLUGS in
app/web/router.py."""
import pytest


def test_ledger_page_requires_auth(client):
    resp = client.get("/ledger")
    assert resp.status_code == 401


@pytest.mark.parametrize("slug", [
    "buchungen", "belege", "kategorien", "import", "kassen",
    "bankkonten", "anlagevermoegen", "ruecklagen", "kassenpruefung", "euer-bericht",
])
def test_ledger_tab_page_renders_for_treasurer(treasurer_client, slug):
    resp = treasurer_client.get(f"/ledger/{slug}")
    assert resp.status_code == 200
    assert "ledgerApp(" in resp.text


def test_ledger_tab_page_renders_for_auditor(auditor_client):
    resp = auditor_client.get("/ledger/kassenpruefung")
    assert resp.status_code == 200


def test_ledger_tab_page_requires_auth(client):
    resp = client.get("/ledger/kassenpruefung")
    assert resp.status_code == 401


def test_ledger_tab_page_unknown_slug_404(treasurer_client):
    resp = treasurer_client.get("/ledger/does-not-exist")
    assert resp.status_code == 404


def test_ledger_page_initial_tab_is_entries(treasurer_client):
    resp = treasurer_client.get("/ledger")
    assert "const INITIAL_TAB = \"entries\";" in resp.text


def test_ledger_tab_page_sets_initial_tab_from_slug(treasurer_client):
    resp = treasurer_client.get("/ledger/kassenpruefung")
    assert "const INITIAL_TAB = \"audit-reports\";" in resp.text
