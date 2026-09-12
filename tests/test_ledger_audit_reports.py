import pytest

# ---------------------------------------------------------------------------
# GET/POST/PUT/DELETE /ledger/audit-reports
#
# Permission model deliberately differs from the rest of the ledger module:
# a plain treasurer can READ these (transparency) but not WRITE them — only
# an explicit auditor-group member or an admin may author/edit/delete a
# Kassenprüfungsprotokoll, since a treasurer authoring the report that
# audits their own bookkeeping would defeat the point (Key Design Decision
# #37). `auditor_client` (conftest.py) is exactly "auditor group, NOT
# treasurer/admin" — the case this permission split exists for.
# ---------------------------------------------------------------------------

def _payload(**overrides):
    body = {
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "audit_date": "2026-03-01",
        "auditors": "Max Mustermann, Erika Musterfrau",
        "findings": "Kasse und Buchführung wurden stichprobenartig geprüft, keine Beanstandungen.",
        "recommends_discharge": True,
    }
    body.update(overrides)
    return body


def test_list_audit_reports_requires_auth(client):
    resp = client.get("/api/v1/ledger/audit-reports")
    assert resp.status_code == 401


def test_treasurer_can_read_audit_reports(treasurer_client, auditor_client):
    auditor_client.post("/api/v1/ledger/audit-reports", json=_payload())
    resp = treasurer_client.get("/api/v1/ledger/audit-reports")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_treasurer_cannot_create_audit_report(treasurer_client):
    resp = treasurer_client.post("/api/v1/ledger/audit-reports", json=_payload())
    assert resp.status_code in (401, 403)


def test_auditor_can_create_audit_report(auditor_client):
    resp = auditor_client.post("/api/v1/ledger/audit-reports", json=_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["auditors"] == "Max Mustermann, Erika Musterfrau"
    assert body["recommends_discharge"] is True
    assert body["created_by"] == "test-auditor-sub"


def test_admin_can_create_audit_report(admin_client):
    resp = admin_client.post("/api/v1/ledger/audit-reports", json=_payload())
    assert resp.status_code == 201


def test_create_audit_report_rejects_period_end_before_start(auditor_client):
    resp = auditor_client.post(
        "/api/v1/ledger/audit-reports",
        json=_payload(period_start="2025-12-31", period_end="2025-01-01"),
    )
    assert resp.status_code == 400


def test_create_audit_report_with_paperless_link(auditor_client):
    resp = auditor_client.post(
        "/api/v1/ledger/audit-reports", json=_payload(paperless_document_id="4711"),
    )
    assert resp.status_code == 201
    assert resp.json()["paperless_document_id"] == "4711"


def test_treasurer_cannot_update_audit_report(treasurer_client, auditor_client):
    report_id = auditor_client.post("/api/v1/ledger/audit-reports", json=_payload()).json()["id"]
    resp = treasurer_client.put(f"/api/v1/ledger/audit-reports/{report_id}", json={"auditors": "Jemand anderes"})
    assert resp.status_code in (401, 403)


def test_auditor_can_update_audit_report(auditor_client):
    report_id = auditor_client.post("/api/v1/ledger/audit-reports", json=_payload()).json()["id"]
    resp = auditor_client.put(
        f"/api/v1/ledger/audit-reports/{report_id}",
        json={"findings": "Nachtrag: eine kleine Differenz von 1 Cent geklärt.", "recommends_discharge": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommends_discharge"] is False
    assert "Nachtrag" in body["findings"]


def test_update_audit_report_rejects_invalid_period(auditor_client):
    report_id = auditor_client.post("/api/v1/ledger/audit-reports", json=_payload()).json()["id"]
    resp = auditor_client.put(
        f"/api/v1/ledger/audit-reports/{report_id}",
        json={"period_start": "2026-01-01"},  # now after the existing period_end (2025-12-31)
    )
    assert resp.status_code == 400


def test_update_audit_report_clears_paperless_link(auditor_client):
    report_id = auditor_client.post(
        "/api/v1/ledger/audit-reports", json=_payload(paperless_document_id="4711"),
    ).json()["id"]
    resp = auditor_client.put(
        f"/api/v1/ledger/audit-reports/{report_id}", json={"clear_paperless_document_id": True},
    )
    assert resp.status_code == 200
    assert resp.json()["paperless_document_id"] is None


def test_update_unknown_audit_report_404(auditor_client):
    resp = auditor_client.put("/api/v1/ledger/audit-reports/9999", json={"auditors": "x"})
    assert resp.status_code == 404


def test_treasurer_cannot_delete_audit_report(treasurer_client, auditor_client):
    report_id = auditor_client.post("/api/v1/ledger/audit-reports", json=_payload()).json()["id"]
    resp = treasurer_client.delete(f"/api/v1/ledger/audit-reports/{report_id}")
    assert resp.status_code in (401, 403)


def test_auditor_can_delete_audit_report(auditor_client):
    report_id = auditor_client.post("/api/v1/ledger/audit-reports", json=_payload()).json()["id"]
    resp = auditor_client.delete(f"/api/v1/ledger/audit-reports/{report_id}")
    assert resp.status_code == 204
    assert auditor_client.get("/api/v1/ledger/audit-reports").json() == []


def test_delete_unknown_audit_report_404(auditor_client):
    resp = auditor_client.delete("/api/v1/ledger/audit-reports/9999")
    assert resp.status_code == 404


def test_list_audit_reports_ordered_by_period_end_desc(auditor_client):
    auditor_client.post("/api/v1/ledger/audit-reports", json=_payload(period_start="2024-01-01", period_end="2024-12-31"))
    auditor_client.post("/api/v1/ledger/audit-reports", json=_payload(period_start="2025-01-01", period_end="2025-12-31"))
    reports = auditor_client.get("/api/v1/ledger/audit-reports").json()
    assert [r["period_end"] for r in reports] == ["2025-12-31", "2024-12-31"]
