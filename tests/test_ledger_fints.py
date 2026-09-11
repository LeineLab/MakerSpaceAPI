"""Phase 3: manual FinTS live-pull.

No test here talks to a real bank (no test access available) — the
`fints_client` unit tests fake the `fints.client` module boundary
(NeedTANResponse/NeedRetryResponse + a fake FinTS3PinTanClient stand-in via
`_make_client`), and the API-level tests mock `app.services.fints_client`'s
public functions directly. Together they cover the session/TAN bookkeeping
this module owns and the wiring into the same dedup pipeline as the file
import — not the FinTS protocol exchange itself.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.ledger import BankAccount
from app.services.bank_statement import ParsedStatementLine

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
def untracked_account(db):
    a = BankAccount(
        iban="DE89370400440532013000", name="Privatkonto",
        opening_balance=Decimal("0.00"), tracked=False,
    )
    db.add(a)
    db.commit()
    return a


def _sample_lines(amount="50.00", reference="E2E-1", purpose="Mitgliedsbeitrag"):
    """Already-converted lines, for tests exercising the API/dedup layer
    directly (those mock app.services.fints_client's public functions, which
    return ParsedStatementLine — the conversion from raw mt940 transactions
    already happened inside fints_client by that point)."""
    return [
        ParsedStatementLine(
            booking_date=date(2026, 3, 1), amount=Decimal(amount), purpose_text=purpose,
            counterparty_name="Max Mustermann", counterparty_iban=None, bank_reference=reference,
        )
    ]


class _FakeAmount:
    def __init__(self, amount):
        self.amount = amount


class _FakeMT940Transaction:
    """Duck-types `mt940.models.Transaction`: a `.data` dict, as consumed by
    `lines_from_mt940_transactions()`. What `_FakeClient.get_transactions`
    below returns — the *raw* shape `FinTS3PinTanClient.get_transactions()`
    itself returns, before fints_client converts it."""
    def __init__(self, amount="50.00", reference="E2E-1", purpose="Mitgliedsbeitrag"):
        self.data = {
            "date": date(2026, 3, 1),
            "entry_date": None,
            "amount": _FakeAmount(Decimal(amount)),
            "purpose": purpose,
            "posting_text": None,
            "applicant_name": "Max Mustermann",
            "recipient_name": None,
            "gvc_applicant_iban": None,
            "applicant_iban": None,
            "end_to_end_reference": reference,
            "bank_reference": None,
        }


# ---------------------------------------------------------------------------
# app/services/fints_client.py — session/TAN bookkeeping, mocked at the
# fints.client module boundary
# ---------------------------------------------------------------------------

def test_start_dialog_requires_product_id(monkeypatch):
    from app.config import settings
    from app.services import fints_client

    monkeypatch.setattr(settings, "FINTS_PRODUCT_ID", "")
    with pytest.raises(fints_client.FinTSNotConfigured):
        fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")


def test_unknown_session_raises(monkeypatch):
    from app.config import settings
    from app.services import fints_client

    monkeypatch.setattr(settings, "FINTS_PRODUCT_ID", "test-product-id")
    with pytest.raises(fints_client.FinTSSessionError):
        fints_client.submit_tan("does-not-exist", "123456")


class _FakeAccount:
    def __init__(self, iban):
        self.iban = iban
        self.bic = "TESTBIC"
        self.accountnumber = "1234567"
        self.subaccount = None
        self.blz = "12030000"


class _FakeNeedTAN:
    def __init__(self, challenge, decoupled=False):
        self.challenge = challenge
        self.decoupled = decoupled

    def get_data(self):
        return b"pending-response-blob"


class _FakeNeedRetryResponse:
    @classmethod
    def from_data(cls, blob):
        assert blob == b"pending-response-blob"
        return "resumed-response-sentinel"


class _FakeClient:
    """Stands in for FinTS3PinTanClient. `steps` is a queue of return values
    consumed in order by successive get_sepa_accounts/get_transactions/send_tan calls."""
    def __init__(self, steps):
        self._steps = list(steps)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def deconstruct(self, including_private=False):
        # Regression guard: including_private=False drops UPD (user parameter
        # data incl. the negotiated TAN mechanism) from the blob, which broke
        # a rebuilt client's very next dialog with a misleading "PIN wrong?"
        # error — confirmed against a real bank. See fints_client.py.
        assert including_private is True
        return b"client-blob"

    def get_sepa_accounts(self):
        return self._steps.pop(0)

    def get_transactions(self, account, date_from, date_to):
        return self._steps.pop(0)

    def send_tan(self, response, tan):
        return self._steps.pop(0)


def _patch_fints(monkeypatch, client):
    from app.config import settings

    monkeypatch.setattr(settings, "FINTS_PRODUCT_ID", "test-product-id")
    monkeypatch.setattr("app.services.fints_client._make_client", lambda *a, **kw: client)
    monkeypatch.setattr("fints.client.NeedTANResponse", _FakeNeedTAN)
    monkeypatch.setattr("fints.client.NeedRetryResponse", _FakeNeedRetryResponse)


def test_start_dialog_no_tan_needed(monkeypatch):
    from app.services import fints_client

    client = _FakeClient([[_FakeAccount("DE02120300000000202051")]])
    _patch_fints(monkeypatch, client)

    result = fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")
    assert result["status"] == "accounts"
    assert result["accounts"][0]["iban"] == "DE02120300000000202051"


def test_start_dialog_then_tan_then_accounts(monkeypatch):
    from app.services import fints_client

    client = _FakeClient([
        _FakeNeedTAN("Please confirm in your app or enter the TAN"),
        [_FakeAccount("DE02120300000000202051")],
    ])
    _patch_fints(monkeypatch, client)

    step1 = fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")
    assert step1["status"] == "tan_required"
    assert "confirm" in step1["challenge"].lower()

    step2 = fints_client.submit_tan(step1["session_id"], "123456")
    assert step2["status"] == "accounts"
    assert step2["session_id"] == step1["session_id"]
    assert step2["accounts"][0]["iban"] == "DE02120300000000202051"


def test_decoupled_poll_until_confirmed(monkeypatch):
    from app.services import fints_client

    client = _FakeClient([
        _FakeNeedTAN("Bitte in der App bestätigen", decoupled=True),
        _FakeNeedTAN("Bitte in der App bestätigen", decoupled=True),  # still not confirmed
        [_FakeAccount("DE02120300000000202051")],  # confirmed
    ])
    _patch_fints(monkeypatch, client)

    step1 = fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")
    assert step1["status"] == "decoupled"

    step2 = fints_client.poll_decoupled(step1["session_id"])
    assert step2["status"] == "decoupled"

    step3 = fints_client.poll_decoupled(step1["session_id"])
    assert step3["status"] == "accounts"


def test_session_survives_a_successful_transaction_fetch_for_more_imports(monkeypatch):
    """One FinTS access can expose multiple accounts (the original motivating
    case for IBAN-based tracking) — importing one account's transactions must
    not tear down the session, so a second account can be fetched right after
    without reconnecting/re-authenticating."""
    from app.services import fints_client

    client = _FakeClient([
        [_FakeAccount("DE02120300000000202051"), _FakeAccount("DE89370400440532013000")],
        [_FakeMT940Transaction(reference="ACC-1-TX")],
        [_FakeMT940Transaction(reference="ACC-2-TX")],
    ])
    _patch_fints(monkeypatch, client)

    accounts_step = fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")
    session_id = accounts_step["session_id"]
    assert len(accounts_step["accounts"]) == 2

    first = fints_client.start_transactions(session_id, "DE02120300000000202051", date(2026, 1, 1), date(2026, 3, 31))
    assert first["status"] == "transactions"

    # Session must still exist and still know both accounts for the second fetch.
    second = fints_client.start_transactions(session_id, "DE89370400440532013000", date(2026, 1, 1), date(2026, 3, 31))
    assert second["status"] == "transactions"
    assert second["iban"] == "DE89370400440532013000"
    assert second["lines"][0].bank_reference == "ACC-2-TX"


def test_transactions_fetch_after_accounts_known(monkeypatch):
    from app.services import fints_client

    client = _FakeClient([
        [_FakeAccount("DE02120300000000202051")],
        [_FakeMT940Transaction()],
    ])
    _patch_fints(monkeypatch, client)

    accounts_step = fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")
    session_id = accounts_step["session_id"]

    tx_step = fints_client.start_transactions(session_id, "DE02120300000000202051", date(2026, 1, 1), date(2026, 3, 31))
    assert tx_step["status"] == "transactions"
    assert tx_step["iban"] == "DE02120300000000202051"
    assert len(tx_step["lines"]) == 1
    assert tx_step["lines"][0].bank_reference == "E2E-1"


def test_transactions_fetch_unknown_iban_in_session(monkeypatch):
    from app.services import fints_client

    client = _FakeClient([[_FakeAccount("DE02120300000000202051")]])
    _patch_fints(monkeypatch, client)

    accounts_step = fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")
    with pytest.raises(fints_client.FinTSSessionError):
        fints_client.start_transactions(accounts_step["session_id"], "DE00000000000000000000", date(2026, 1, 1), date(2026, 3, 31))


def test_transactions_fetch_requires_tan_then_completes(monkeypatch):
    from app.services import fints_client

    client = _FakeClient([
        [_FakeAccount("DE02120300000000202051")],
        _FakeNeedTAN("Please confirm the transaction fetch"),
        [_FakeMT940Transaction()],
    ])
    _patch_fints(monkeypatch, client)

    accounts_step = fints_client.start_dialog("https://bank.example/fints", "12030000", "user", "1234")
    session_id = accounts_step["session_id"]

    tx_step1 = fints_client.start_transactions(session_id, "DE02120300000000202051", date(2026, 1, 1), date(2026, 3, 31))
    assert tx_step1["status"] == "tan_required"

    tx_step2 = fints_client.submit_tan(session_id, "999999")
    assert tx_step2["status"] == "transactions"
    assert tx_step2["iban"] == "DE02120300000000202051"


# ---------------------------------------------------------------------------
# API layer — app.services.fints_client mocked directly
# ---------------------------------------------------------------------------

def test_fints_start_requires_treasurer(client):
    resp = client.post("/api/v1/ledger/fints/start", json={
        "server": "https://bank.example/fints", "bank_identifier": "12030000", "login": "u", "pin": "1234",
    })
    assert resp.status_code == 401


def test_fints_not_configured_returns_400(treasurer_client):
    # FINTS_PRODUCT_ID defaults to "" — real code path, no mocking needed.
    resp = treasurer_client.post("/api/v1/ledger/fints/start", json={
        "server": "https://bank.example/fints", "bank_identifier": "12030000", "login": "u", "pin": "1234",
    })
    assert resp.status_code == 400


def test_fints_bank_connection_error_returns_502_not_500(treasurer_client):
    """A wrong PIN, an offline bank, a dropped connection — anything the FinTS
    protocol/network layer itself raises — must surface as a clean error, not
    an unhandled 500."""
    import fints.exceptions

    with patch("app.services.fints_client.start_dialog") as mock_start:
        mock_start.side_effect = fints.exceptions.FinTSClientPINError("Wrong PIN")
        resp = treasurer_client.post("/api/v1/ledger/fints/start", json={
            "server": "https://bank.example/fints", "bank_identifier": "12030000", "login": "u", "pin": "wrong",
        })
    assert resp.status_code == 502


def test_fints_start_success_annotates_known_account(treasurer_client, bank_account):
    with patch("app.services.fints_client.start_dialog") as mock_start:
        mock_start.return_value = {
            "session_id": "sess-1", "status": "accounts",
            "accounts": [{"iban": bank_account.iban, "bic": None, "account_number": "1", "subaccount": None, "blz": "123"}],
        }
        resp = treasurer_client.post("/api/v1/ledger/fints/start", json={
            "server": "https://bank.example/fints", "bank_identifier": "12030000", "login": "u", "pin": "1234",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["accounts"][0]["tracked"] is True
    assert data["accounts"][0]["bank_account_id"] == bank_account.id


def test_fints_start_success_flags_unknown_account(treasurer_client):
    with patch("app.services.fints_client.start_dialog") as mock_start:
        mock_start.return_value = {
            "session_id": "sess-1", "status": "accounts",
            "accounts": [{"iban": "DE00000000000000000000", "bic": None, "account_number": "1", "subaccount": None, "blz": "123"}],
        }
        resp = treasurer_client.post("/api/v1/ledger/fints/start", json={
            "server": "https://bank.example/fints", "bank_identifier": "12030000", "login": "u", "pin": "1234",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["accounts"][0]["tracked"] is None
    assert data["accounts"][0]["bank_account_id"] is None


def test_fints_start_tan_required_passthrough(treasurer_client):
    with patch("app.services.fints_client.start_dialog") as mock_start:
        mock_start.return_value = {"session_id": "sess-1", "status": "tan_required", "challenge": "Enter TAN"}
        resp = treasurer_client.post("/api/v1/ledger/fints/start", json={
            "server": "https://bank.example/fints", "bank_identifier": "12030000", "login": "u", "pin": "1234",
        })
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "sess-1", "status": "tan_required", "challenge": "Enter TAN"}


def test_fints_tan_unknown_session_404(treasurer_client):
    resp = treasurer_client.post("/api/v1/ledger/fints/tan", json={"session_id": "nope", "tan": "123456"})
    assert resp.status_code == 404


def test_fints_import_unknown_account_404(treasurer_client):
    with patch("app.services.fints_client.start_transactions") as mock_fetch:
        mock_fetch.return_value = {
            "session_id": "sess-1", "status": "transactions",
            "iban": "DE00000000000000000000", "lines": _sample_lines(),
        }
        resp = treasurer_client.post(
            "/api/v1/ledger/fints/accounts/DE00000000000000000000/import",
            json={"session_id": "sess-1", "date_from": "2026-01-01", "date_to": "2026-03-31"},
        )
    assert resp.status_code == 404


def test_fints_import_untracked_account_400(treasurer_client, untracked_account):
    with patch("app.services.fints_client.start_transactions") as mock_fetch:
        mock_fetch.return_value = {
            "session_id": "sess-1", "status": "transactions",
            "iban": untracked_account.iban, "lines": _sample_lines(),
        }
        resp = treasurer_client.post(
            f"/api/v1/ledger/fints/accounts/{untracked_account.iban}/import",
            json={"session_id": "sess-1", "date_from": "2026-01-01", "date_to": "2026-03-31"},
        )
    assert resp.status_code == 400


def test_fints_import_success_stages_lines(treasurer_client, bank_account):
    with patch("app.services.fints_client.start_transactions") as mock_fetch:
        mock_fetch.return_value = {
            "session_id": "sess-1", "status": "transactions",
            "iban": bank_account.iban, "lines": _sample_lines(),
        }
        resp = treasurer_client.post(
            f"/api/v1/ledger/fints/accounts/{bank_account.iban}/import",
            json={"session_id": "sess-1", "date_from": "2026-01-01", "date_to": "2026-03-31"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_count"] == 1
    assert data["duplicate_count"] == 0

    lines = treasurer_client.get("/api/v1/ledger/import/lines").json()
    assert len(lines) == 1
    assert lines[0]["bank_reference"] == "E2E-1"


def test_fints_import_tan_required_returns_challenge_not_summary(treasurer_client, bank_account):
    with patch("app.services.fints_client.start_transactions") as mock_fetch:
        mock_fetch.return_value = {"session_id": "sess-1", "status": "tan_required", "challenge": "Confirm the fetch"}
        resp = treasurer_client.post(
            f"/api/v1/ledger/fints/accounts/{bank_account.iban}/import",
            json={"session_id": "sess-1", "date_from": "2026-01-01", "date_to": "2026-03-31"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "sess-1", "status": "tan_required", "challenge": "Confirm the fetch"}


def test_fints_tan_completing_a_transaction_fetch_stages_lines(treasurer_client, bank_account):
    """The TAN that resolves a pending operation might belong to the
    transaction fetch, not the account listing — /fints/tan must handle that
    the same way the dedicated import endpoint does, not just pass through
    tan_required/accounts shapes."""
    with patch("app.services.fints_client.submit_tan") as mock_tan:
        mock_tan.return_value = {
            "session_id": "sess-1", "status": "transactions",
            "iban": bank_account.iban, "lines": _sample_lines(reference="E2E-2"),
        }
        resp = treasurer_client.post("/api/v1/ledger/fints/tan", json={"session_id": "sess-1", "tan": "123456"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_count"] == 1


def test_fints_cross_source_dedup_with_file_import(treasurer_client, bank_account):
    """A transaction already imported via file upload must be recognized as a
    duplicate when the same booking later comes in via FinTS (same account,
    same dedup pool) — and vice versa."""
    mt940_sample = (
        b":20:STARTUMSE\n"
        b":25:DE02120300000000202051\n"
        b":28C:1/1\n"
        b":60F:C260101EUR1000,00\n"
        b":61:2603010301C50,00NTRFNONREF\n"
        b":86:166?00SEPA GUTSCHRIFT?20EREF+E2E-1?21SVWZ+Mitgliedsbeitrag\n"
        b":62F:C260301EUR1050,00\n"
    )
    treasurer_client.post(
        f"/api/v1/ledger/import/file?bank_account_id={bank_account.id}&filename=sample.sta",
        content=mt940_sample,
        headers={"Content-Type": "application/octet-stream"},
    )

    with patch("app.services.fints_client.start_transactions") as mock_fetch:
        mock_fetch.return_value = {
            "session_id": "sess-1", "status": "transactions",
            "iban": bank_account.iban, "lines": _sample_lines(reference="E2E-1"),
        }
        resp = treasurer_client.post(
            f"/api/v1/ledger/fints/accounts/{bank_account.iban}/import",
            json={"session_id": "sess-1", "date_from": "2026-01-01", "date_to": "2026-03-31"},
        )
    data = resp.json()
    assert data["new_count"] == 0
    assert data["duplicate_count"] == 1


def test_fints_shared_placeholder_reference_does_not_cross_flag_different_amounts(treasurer_client, bank_account):
    """Real-world bug (2026-09-11, found via a real FinTS pull): the bank
    fills in the SEPA placeholder NOTPROVIDED for any transaction without an
    explicit end-to-end reference, so many distinct transactions share that
    same "reference" — the dedup pipeline (shared with file import) must not
    treat them as duplicates of each other just because of that."""
    lines = _sample_lines(amount="25.00", reference="NOTPROVIDED") + _sample_lines(amount="40.00", reference="NOTPROVIDED")
    with patch("app.services.fints_client.start_transactions") as mock_fetch:
        mock_fetch.return_value = {
            "session_id": "sess-1", "status": "transactions",
            "iban": bank_account.iban, "lines": lines,
        }
        resp = treasurer_client.post(
            f"/api/v1/ledger/fints/accounts/{bank_account.iban}/import",
            json={"session_id": "sess-1", "date_from": "2026-01-01", "date_to": "2026-03-31"},
        )
    data = resp.json()
    assert data["new_count"] == 2
    assert data["duplicate_count"] == 0


def test_auditor_cannot_start_fints(auditor_client):
    resp = auditor_client.post("/api/v1/ledger/fints/start", json={
        "server": "https://bank.example/fints", "bank_identifier": "12030000", "login": "u", "pin": "1234",
    })
    assert resp.status_code in (401, 403)


def test_ledger_config_reports_fints_disabled_by_default(auditor_client):
    resp = auditor_client.get("/api/v1/ledger/config")
    assert resp.json()["fints_enabled"] is False


def test_ledger_config_reports_fints_enabled(auditor_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "FINTS_PRODUCT_ID", "test-product-id")
    resp = auditor_client.get("/api/v1/ledger/config")
    assert resp.json()["fints_enabled"] is True


# ---------------------------------------------------------------------------
# FinTS bank presets (server/BLZ/name only — never login/PIN)
# ---------------------------------------------------------------------------

def test_create_and_list_fints_preset(treasurer_client):
    resp = treasurer_client.post("/api/v1/ledger/fints/presets", json={
        "name": "Beispielbank", "server": "https://banking-fints.example.com/fints30",
        "bank_identifier": "12030000",
    })
    assert resp.status_code == 201
    preset = resp.json()
    assert preset["name"] == "Beispielbank"
    assert "login" not in preset and "pin" not in preset

    listed = treasurer_client.get("/api/v1/ledger/fints/presets").json()
    assert listed == [preset]


def test_auditor_can_list_but_not_create_fints_preset(auditor_client):
    assert auditor_client.get("/api/v1/ledger/fints/presets").status_code == 200

    resp = auditor_client.post("/api/v1/ledger/fints/presets", json={
        "name": "Beispielbank", "server": "https://bank.example/fints", "bank_identifier": "12030000",
    })
    assert resp.status_code in (401, 403)


def test_delete_fints_preset(treasurer_client):
    created = treasurer_client.post("/api/v1/ledger/fints/presets", json={
        "name": "Beispielbank", "server": "https://bank.example/fints", "bank_identifier": "12030000",
    }).json()

    resp = treasurer_client.delete(f"/api/v1/ledger/fints/presets/{created['id']}")
    assert resp.status_code == 204
    assert treasurer_client.get("/api/v1/ledger/fints/presets").json() == []


def test_delete_unknown_fints_preset_404(treasurer_client):
    resp = treasurer_client.delete("/api/v1/ledger/fints/presets/999")
    assert resp.status_code == 404
