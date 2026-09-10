"""Manual FinTS live-pull (Phase 3): a treasurer-triggered, non-persisted bank
session — no scheduled automation, no bank credentials ever written to the
database or logs. The PIN and the live FinTS dialog state exist only in this
process's memory, for a short TTL, keyed by an opaque session id handed back
to the frontend between requests.

python-fints's own serialization primitives are explicitly designed for this
stateless-across-requests use case:
- `client.deconstruct()` -> bytes (no PIN, no bound closures; restore via
  `FinTS3PinTanClient(..., from_data=blob)`)
- `NeedTANResponse.get_data()` -> bytes, restored via `NeedRetryResponse.from_data()`
  and completed with `client.send_tan(response, tan)`

Both `get_sepa_accounts()` and `get_transactions()` can return a `NeedTANResponse`
instead of their result — banks vary on whether a TAN is required for a plain
read, so every call site here has to handle that possibility. `decoupled=True`
challenges (pushTAN-style app confirmation) are resolved by resubmitting with
an empty TAN until the bank reports it's been confirmed — that's what
`poll_decoupled()` does.

None of this has been exercised against a real bank (no test access available)
— see tests/test_ledger_fints.py, which mocks FinTS3PinTanClient instead. The
control flow here follows the library's documented/source-level behavior as
closely as possible, but treat the very first real-bank run as the actual
verification of this module.
"""
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Optional

from app.config import settings
from app.services.bank_statement import ParsedStatementLine, lines_from_mt940_transactions


class FinTSNotConfigured(Exception):
    pass


class FinTSSessionError(Exception):
    """Session expired, unknown, or has no pending step matching the request."""


_SESSION_TTL = timedelta(minutes=10)


@dataclass
class _Session:
    client_blob: bytes
    bank_identifier: str
    server: str
    login: str
    pin: str
    accounts: Optional[list[dict]] = None
    pending_response_blob: Optional[bytes] = None
    pending_action: Optional[str] = None  # "accounts" | "transactions"
    pending_account: Optional[dict] = None
    pending_date_from: Optional[str] = None
    pending_date_to: Optional[str] = None
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + _SESSION_TTL)


_sessions: dict[str, _Session] = {}


def _cleanup() -> None:
    now = datetime.now(UTC)
    for key in [k for k, s in _sessions.items() if s.expires_at < now]:
        del _sessions[key]


def _get_session(session_id: str) -> _Session:
    _cleanup()
    session = _sessions.get(session_id)
    if not session:
        raise FinTSSessionError("FinTS session expired or unknown — please start again")
    return session


def _require_configured() -> None:
    if not settings.FINTS_PRODUCT_ID:
        raise FinTSNotConfigured("FinTS live-pull is not configured (FINTS_PRODUCT_ID is empty)")


def _make_client(bank_identifier: str, login: str, pin: str, server: str, from_data: Optional[bytes] = None):
    from fints.client import FinTS3PinTanClient

    kwargs = {"product_id": settings.FINTS_PRODUCT_ID}
    if from_data is not None:
        kwargs["from_data"] = from_data
    return FinTS3PinTanClient(bank_identifier, login, pin, server, **kwargs)


def _account_to_dict(account) -> dict:
    return {
        "iban": account.iban,
        "bic": account.bic,
        "account_number": account.accountnumber,
        "subaccount": account.subaccount,
        "blz": account.blz,
    }


def _sepa_account_from_dict(d: dict):
    from fints.models import SEPAAccount

    return SEPAAccount(
        iban=d["iban"], bic=d["bic"], accountnumber=d["account_number"],
        subaccount=d["subaccount"], blz=d["blz"],
    )


def _settle(session_id: str, client, bank_identifier: str, server: str, login: str, pin: str,
            action: str, response, pending_extra: Optional[tuple] = None) -> dict:
    """Turn a python-fints call result into the wizard's step response, storing
    (or clearing) the session as appropriate."""
    from fints.client import NeedTANResponse

    if isinstance(response, NeedTANResponse):
        session = _Session(
            client_blob=client.deconstruct(),
            bank_identifier=bank_identifier, server=server, login=login, pin=pin,
            pending_response_blob=response.get_data(), pending_action=action,
        )
        if pending_extra:
            session.pending_account, session.pending_date_from, session.pending_date_to = pending_extra
        existing = _sessions.get(session_id)
        if existing and existing.accounts:
            session.accounts = existing.accounts
        _sessions[session_id] = session
        return {
            "session_id": session_id,
            "status": "decoupled" if response.decoupled else "tan_required",
            "challenge": response.challenge,
        }

    if action == "accounts":
        accounts = [_account_to_dict(a) for a in response]
        _sessions[session_id] = _Session(
            client_blob=client.deconstruct(),
            bank_identifier=bank_identifier, server=server, login=login, pin=pin,
            accounts=accounts,
        )
        return {"session_id": session_id, "status": "accounts", "accounts": accounts}

    # action == "transactions": nothing left to *resume* for this operation, but
    # keep the session alive with its known accounts — the same access may have
    # more accounts worth importing, and re-authenticating for each one would
    # defeat the point of listing them together in step 1.
    existing = _sessions.get(session_id)
    _sessions[session_id] = _Session(
        client_blob=client.deconstruct(),
        bank_identifier=bank_identifier, server=server, login=login, pin=pin,
        accounts=existing.accounts if existing else None,
    )
    lines: list[ParsedStatementLine] = lines_from_mt940_transactions(response)
    iban = pending_extra[0]["iban"] if pending_extra else None
    return {"session_id": session_id, "status": "transactions", "iban": iban, "lines": lines}


def start_dialog(server: str, bank_identifier: str, login: str, pin: str) -> dict:
    """Step 1: connect and list accounts. May come back as tan_required/decoupled."""
    _require_configured()
    client = _make_client(bank_identifier, login, pin, server)
    with client:
        response = client.get_sepa_accounts()
    return _settle(secrets.token_urlsafe(24), client, bank_identifier, server, login, pin, "accounts", response)


def submit_tan(session_id: str, tan: str) -> dict:
    """Step 2 (only if step 1 or the transactions fetch came back tan_required):
    submit the typed TAN and continue whichever operation was pending."""
    session = _get_session(session_id)
    if not session.pending_response_blob or not session.pending_action:
        raise FinTSSessionError("No pending TAN challenge for this session")

    from fints.client import NeedRetryResponse

    client = _make_client(session.bank_identifier, session.login, session.pin, session.server, from_data=session.client_blob)
    pending_response = NeedRetryResponse.from_data(session.pending_response_blob)
    with client:
        response = client.send_tan(pending_response, tan)

    pending_extra = None
    if session.pending_action == "transactions":
        pending_extra = (session.pending_account, session.pending_date_from, session.pending_date_to)
    return _settle(
        session_id, client, session.bank_identifier, session.server, session.login, session.pin,
        session.pending_action, response, pending_extra,
    )


def poll_decoupled(session_id: str) -> dict:
    """For decoupled (pushTAN-app) challenges: re-check without a typed TAN."""
    return submit_tan(session_id, "")


def start_transactions(session_id: str, iban: str, date_from: date, date_to: date) -> dict:
    """Step 3: fetch transactions for one of the accounts discovered in step 1.
    May also come back as tan_required/decoupled (some banks require a TAN for
    the transaction read specifically, even if the account listing didn't)."""
    session = _get_session(session_id)
    if not session.accounts:
        raise FinTSSessionError("No accounts known for this session — start the dialog first")
    account_dict = next((a for a in session.accounts if a["iban"] == iban), None)
    if not account_dict:
        raise FinTSSessionError(f"IBAN {iban} was not among the accounts found for this session")

    client = _make_client(session.bank_identifier, session.login, session.pin, session.server, from_data=session.client_blob)
    account = _sepa_account_from_dict(account_dict)
    with client:
        response = client.get_transactions(account, date_from, date_to)
    return _settle(
        session_id, client, session.bank_identifier, session.server, session.login, session.pin,
        "transactions", response, (account_dict, date_from.isoformat(), date_to.isoformat()),
    )
