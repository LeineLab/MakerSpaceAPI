"""Key Design Decision #52: ledger sync tokens — narrowly-scoped, unattended
FinTS-sync auth for the external scripts/ledger_fints_sync.py script. No
FinTS/bank credentials are ever involved here — only the token management
(treasurer-authenticated) and the three script-facing endpoints it protects.
"""
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.ledger import BankAccount, LedgerImportBatch, LedgerImportLine, LedgerImportSource, LedgerImportStatus, LedgerSyncToken

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bank_account(db):
    a = BankAccount(iban="DE02120300000000202051", name="Vereinskonto", opening_balance=Decimal("0.00"))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def other_account(db):
    a = BankAccount(iban="DE89370400440532013000", name="Privatkonto", opening_balance=Decimal("0.00"))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def sync_token(db, bank_account):
    """A plain, active, unpaused sync token scoped to `bank_account`. Returns
    (plaintext_token, LedgerSyncToken row)."""
    from app.auth.tokens import generate_api_token

    plaintext, token_hash = generate_api_token()
    token = LedgerSyncToken(
        name="Test Sync", token_hash=token_hash, bank_account_id=bank_account.id,
        created_by="test-treasurer-sub",
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return plaintext, token


# ---------------------------------------------------------------------------
# POST/GET/PUT/DELETE /ledger/sync-tokens (treasurer-managed)
# ---------------------------------------------------------------------------

def test_create_sync_token_requires_treasurer(auditor_client, bank_account):
    resp = auditor_client.post(
        "/api/v1/ledger/sync-tokens",
        json={"name": "Cron Sync", "bank_account_id": bank_account.id},
    )
    assert resp.status_code in (401, 403)


def test_create_sync_token_success_shows_plaintext_once(treasurer_client, bank_account):
    resp = treasurer_client.post(
        "/api/v1/ledger/sync-tokens",
        json={"name": "Cron Sync", "bank_account_id": bank_account.id},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["bank_account_id"] == bank_account.id
    assert data["active"] is True
    assert data["paused"] is False
    assert data["consecutive_failures"] == 0
    assert isinstance(data["token"], str) and len(data["token"]) > 10


def test_create_sync_token_unknown_account_404(treasurer_client):
    resp = treasurer_client.post(
        "/api/v1/ledger/sync-tokens",
        json={"name": "Cron Sync", "bank_account_id": 999},
    )
    assert resp.status_code == 404


def test_list_sync_tokens_never_includes_plaintext(treasurer_client, sync_token):
    resp = treasurer_client.get("/api/v1/ledger/sync-tokens")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "token" not in data[0]


def test_list_sync_tokens_filters_by_bank_account_id(treasurer_client, db, sync_token, other_account):
    from app.auth.tokens import generate_api_token

    _, other_token_hash = generate_api_token()
    db.add(LedgerSyncToken(
        name="Other", token_hash=other_token_hash, bank_account_id=other_account.id,
        created_by="test-treasurer-sub",
    ))
    db.commit()

    _, token = sync_token
    resp = treasurer_client.get(f"/api/v1/ledger/sync-tokens?bank_account_id={token.bank_account_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["bank_account_id"] == token.bank_account_id


def test_update_sync_token_rename_and_deactivate(treasurer_client, sync_token):
    _, token = sync_token
    resp = treasurer_client.put(
        f"/api/v1/ledger/sync-tokens/{token.id}",
        json={"name": "Renamed", "active": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Renamed"
    assert data["active"] is False


def test_update_sync_token_resume_clears_pause_and_failures(treasurer_client, db, sync_token):
    _, token = sync_token
    token.paused = True
    token.consecutive_failures = 5
    db.commit()

    resp = treasurer_client.put(f"/api/v1/ledger/sync-tokens/{token.id}", json={"resume": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["paused"] is False
    assert data["consecutive_failures"] == 0


def test_update_unknown_sync_token_404(treasurer_client):
    resp = treasurer_client.put("/api/v1/ledger/sync-tokens/999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_sync_token(treasurer_client, db, sync_token):
    plaintext, token = sync_token
    resp = treasurer_client.delete(f"/api/v1/ledger/sync-tokens/{token.id}")
    assert resp.status_code == 204
    assert db.query(LedgerSyncToken).filter(LedgerSyncToken.id == token.id).first() is None

    # And the now-deleted token can no longer authenticate against /sync/account
    resp = treasurer_client.get(
        "/api/v1/ledger/sync/account", headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /ledger/sync/account, POST /ledger/sync/import, POST /ledger/sync/report-error
# ---------------------------------------------------------------------------

def test_sync_account_requires_valid_token(client):
    resp = client.get("/api/v1/ledger/sync/account")
    assert resp.status_code == 401

    resp = client.get("/api/v1/ledger/sync/account", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_sync_account_returns_scoped_account_and_last_transaction_date(client, db, sync_token, bank_account):
    plaintext, _ = sync_token
    batch = LedgerImportBatch(bank_account_id=bank_account.id, source=LedgerImportSource.file, imported_by="tester")
    db.add(batch)
    db.flush()
    db.add(LedgerImportLine(
        batch_id=batch.id, bank_account_id=bank_account.id, booking_date=date(2026, 3, 10),
        amount=Decimal("-1.00"), dedup_hash="h1", status=LedgerImportStatus.new,
    ))
    db.commit()

    resp = client.get("/api/v1/ledger/sync/account", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == bank_account.id
    assert data["iban"] == bank_account.iban
    assert data["last_transaction_date"] == "2026-03-10"


def test_sync_account_none_when_nothing_imported_yet(client, sync_token):
    plaintext, _ = sync_token
    resp = client.get("/api/v1/ledger/sync/account", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.json()["last_transaction_date"] is None


def test_sync_account_rejects_paused_token(client, db, sync_token):
    plaintext, token = sync_token
    token.paused = True
    token.consecutive_failures = 3
    db.commit()

    resp = client.get("/api/v1/ledger/sync/account", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 403
    assert "paused" in resp.json()["detail"].lower()


def test_sync_account_rejects_inactive_token(client, db, sync_token):
    plaintext, token = sync_token
    token.active = False
    db.commit()

    resp = client.get("/api/v1/ledger/sync/account", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_sync_import_stages_lines_and_resets_failure_counter(client, db, sync_token):
    plaintext, token = sync_token
    token.consecutive_failures = 2
    db.commit()

    resp = client.post(
        "/api/v1/ledger/sync/import",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"lines": [
            {"booking_date": "2026-03-10", "amount": "-12.34", "purpose_text": "Wareneinkauf",
             "counterparty_name": "Beispiel GmbH", "counterparty_iban": None, "bank_reference": "REF1"},
        ]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_count"] == 1
    assert data["duplicate_count"] == 0

    db.refresh(token)
    assert token.consecutive_failures == 0
    assert token.last_success_at is not None

    staged = db.query(LedgerImportLine).filter(LedgerImportLine.bank_reference == "REF1").first()
    assert staged is not None
    assert staged.amount == Decimal("-12.34")


def test_sync_import_dedups_against_already_staged_line(client, db, sync_token, bank_account):
    plaintext, _ = sync_token
    batch = LedgerImportBatch(bank_account_id=bank_account.id, source=LedgerImportSource.fints, imported_by="tester")
    db.add(batch)
    db.flush()
    db.add(LedgerImportLine(
        batch_id=batch.id, bank_account_id=bank_account.id, booking_date=date(2026, 3, 10),
        amount=Decimal("-5.00"), purpose_text="Bereits importiert", bank_reference="DUPREF",
        dedup_hash="irrelevant-for-ref-match", status=LedgerImportStatus.new,
    ))
    db.commit()

    resp = client.post(
        "/api/v1/ledger/sync/import",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"lines": [
            {"booking_date": "2026-03-10", "amount": "-5.00", "purpose_text": "Bereits importiert",
             "bank_reference": "DUPREF"},
        ]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_count"] == 0
    assert data["duplicate_count"] == 1


def test_sync_import_rejects_paused_token(client, db, sync_token):
    plaintext, token = sync_token
    token.paused = True
    db.commit()

    resp = client.post(
        "/api/v1/ledger/sync/import",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"lines": []},
    )
    assert resp.status_code == 403


def test_sync_report_error_increments_failures(client, sync_token, db):
    plaintext, token = sync_token
    resp = client.post(
        "/api/v1/ledger/sync/report-error",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"message": "FinTS connection refused"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["consecutive_failures"] == 1
    assert data["paused"] is False
    assert data["last_error"] == "FinTS connection refused"


def test_sync_report_error_pauses_at_threshold(client, sync_token):
    plaintext, _ = sync_token
    for _ in range(3):
        resp = client.post(
            "/api/v1/ledger/sync/report-error",
            headers={"Authorization": f"Bearer {plaintext}"},
            json={"message": "FinTS connection refused"},
        )
    assert resp.status_code == 200
    assert resp.json()["paused"] is True
    assert resp.json()["consecutive_failures"] == 3


def test_sync_report_error_works_even_when_already_paused(client, db, sync_token):
    plaintext, token = sync_token
    token.paused = True
    token.consecutive_failures = 3
    db.commit()

    resp = client.post(
        "/api/v1/ledger/sync/report-error",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"message": "still broken"},
    )
    assert resp.status_code == 200
    assert resp.json()["consecutive_failures"] == 4


def test_sync_token_scoped_to_its_own_account_only(client, db, sync_token, other_account):
    """A token minted for one account has no way to name a different one —
    there's no account_id in the request at all, only in the token itself."""
    plaintext, token = sync_token
    resp = client.get("/api/v1/ledger/sync/account", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.json()["id"] != other_account.id


def test_sync_token_cannot_be_used_as_treasurer_session(client, sync_token):
    """A sync token is not a JWT and must not grant access to the normal
    treasurer-authenticated ledger endpoints."""
    plaintext, _ = sync_token
    resp = client.get("/api/v1/ledger/accounts", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_resumed_token_can_sync_again(client, db, sync_token, treasurer_client):
    plaintext, token = sync_token
    token.paused = True
    token.consecutive_failures = 3
    db.commit()

    resume_resp = treasurer_client.put(f"/api/v1/ledger/sync-tokens/{token.id}", json={"resume": True})
    assert resume_resp.status_code == 200

    resp = client.get("/api/v1/ledger/sync/account", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200
