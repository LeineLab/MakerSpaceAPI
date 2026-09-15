#!/usr/bin/env python3
"""
Unattended FinTS sync for ONE bank account — Key Design Decision #52.

Run this on a host YOU trust (your own machine, a small cron box) — never on
the MakerSpaceAPI server itself. Your bank login/PIN live only in this
process's environment for the duration of one run; MakerSpaceAPI never sees
them, stores them, or asks for them again. The only thing this script hands
to the API is a narrowly-scoped "ledger sync token" (create one under the
Bankkonten tab in the web UI, as a treasurer) — a token that can only ever
(1) read the one bank account it was minted for, including how far the
ledger's own import history already reaches, and (2) submit newly-fetched
transactions for staging. It cannot read anything else in the ledger, book
anything, or be read back once issued (hashed at rest, same as a machine's
API token).

WHAT THIS DOES, EACH RUN
-------------------------
1. GET {LEDGER_API_URL}/api/v1/ledger/sync/account with the sync token ->
   learns which IBAN it's scoped to and its last_transaction_date (the most
   recent booking_date already staged for this account, across every import
   source — file, CSV, the manual FinTS wizard, or a previous run of this
   script).
2. Computes a pull window from that:
     date_from = last_transaction_date + 1 day (or 30 days back if nothing
                 has ever been imported for this account yet)
     date_to   = today - 3 days
   This is NOT independently invented here — it mirrors
   fintsDefaultDatesForAccount() in app/web/templates/ledger/index.html
   exactly (see Key Design Decision #51). date_to stays a few days short of
   today so a transaction near the edge is only pulled once its Wertstellung
   has actually settled with the bank; date_from tracks the ledger's own
   history (not a fixed lookback) so re-running this script daily, weekly,
   or after a gap never re-fetches an already-imported range — the whole
   point being to avoid the guaranteed-duplicate-flagging that motivated
   #51 in the first place.
3. Connects via FinTS (python-fints) and fetches transactions for that
   window, converting them with the exact same
   app.services.bank_statement.lines_from_mt940_transactions() the rest of
   this app already uses for file imports and the manual FinTS wizard — so
   the sign/IBAN-parsing fixes documented there (Key Design Decision #19)
   apply identically here. This is why the script needs to run from within
   a checkout of this repo (with its venv active), not as a standalone
   single file — reusing that function is deliberate, not incidental.
4. POSTs the parsed lines to /api/v1/ledger/sync/import, which stages them
   through the exact same dedup pipeline as every other import path.
5. On any failure — a FinTS connection error, a TAN/SCA challenge this
   script can't resolve unattended, or an API-side error — reports it via
   /api/v1/ledger/sync/report-error and exits non-zero. After 3 consecutive
   failures the token auto-pauses server-side; every subsequent run's very
   first call (step 1) then fails fast with a clear "paused" error until a
   treasurer resumes it in the web UI. This is deliberate: an unattended
   script quietly failing against a broken connection for weeks is worse
   than a treasurer having to click "resume" a little eagerly.

LIMITATIONS — READ BEFORE RELYING ON THIS UNATTENDED
------------------------------------------------------
- No TAN/SCA handling. A bank that demands a fresh TAN for this specific
  pull (a typed-TAN challenge, or a "decoupled" pushTAN-app confirmation)
  cannot be completed by this script — there is no human here to type a TAN
  or tap "confirm" on a phone. The run reports the challenge as an error and
  stops; nothing about that is a bug to fix here, it's inherent to FinTS/
  PSD2 strong-customer-authentication. In practice this mostly means:
  running the sync frequently (so each pull's date range stays short,
  thanks to date_from tracking above) keeps you inside whatever recurring-
  access SCA exemption window your bank grants — see Key Design Decision
  #26 for when this app last saw a >90-day fetch trigger SCA against a real
  bank, and a short window not. A one-time TAN-registration/FinTS-access
  setup step on your bank's own online-banking site still has to happen
  before ANY of this works, same as it does before the manual web wizard.
- One account per run. A sync token is scoped to exactly one bank account
  by design (least privilege — see the token's own docstring in
  app/models/ledger.py); if you have several accounts to automate, create
  one token per account and run this script once per token (e.g. several
  cron lines, or a small wrapper loop of your own around this script).

CONFIGURATION — environment variables only, never CLI flags
--------------------------------------------------------------
Nothing sensitive is ever accepted as a command-line argument here — a CLI
arg is visible to every other process on the same host via `ps`, and often
ends up in shell history too. Set these as real environment variables
(a `.env` sourced into cron's environment, systemd's `Environment=`/
`EnvironmentFile=`, etc.) instead:

  LEDGER_API_URL      Base URL of your MakerSpaceAPI instance, e.g.
                       https://api.example.com  (required)
  LEDGER_SYNC_TOKEN    The plaintext sync token from the web UI  (required)
  FINTS_SERVER         FinTS server URL for the bank  (required)
  FINTS_BLZ             Bankleitzahl  (required)
  FINTS_LOGIN           FinTS login / Anmeldename  (required)
  FINTS_PIN             FinTS PIN  (required)
  FINTS_PRODUCT_ID      Your registered FinTS product id — see
                        https://www.hbci-zka.de/register/prod_register.htm
                        (required)

USAGE
-----
    export LEDGER_API_URL=https://api.example.com
    export LEDGER_SYNC_TOKEN=...
    export FINTS_SERVER=... FINTS_BLZ=... FINTS_LOGIN=... FINTS_PIN=... FINTS_PRODUCT_ID=...

    python scripts/ledger_fints_sync.py             # fetch + stage
    python scripts/ledger_fints_sync.py --dry-run    # fetch + print only, nothing is submitted or reported

Example cron entry (daily at 6am; keep the env vars in a file cron sources,
not inline in the crontab, so they're not world-readable via `crontab -l`):
    0 6 * * * . /etc/ledger_fints_sync.env && /path/to/.venv/bin/python \\
      /path/to/MakerSpaceAPI/scripts/ledger_fints_sync.py \\
      >> /var/log/ledger_fints_sync.log 2>&1
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def _api_request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw else {}
    except HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"{method} {url} -> {e}") from e


def _add_days_iso(iso_date: str, days: int) -> str:
    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


def default_date_range(last_transaction_date: str | None) -> tuple[str, str]:
    """Mirrors fintsDefaultDatesForAccount() in
    app/web/templates/ledger/index.html exactly — see the module docstring
    and Key Design Decision #51/#52. There is deliberately no shared
    implementation between the two (one's JS, one's Python); keep both in
    sync by hand if this rule ever changes."""
    today = date.today().isoformat()
    date_to = _add_days_iso(today, -3)
    if last_transaction_date:
        date_from = _add_days_iso(last_transaction_date, 1)
        if date_from > date_to:
            date_from = date_to
    else:
        date_from = _add_days_iso(date_to, -30)
    return date_from, date_to


def _report_error(api_url: str, token: str, message: str) -> None:
    try:
        _api_request("POST", f"{api_url}/api/v1/ledger/sync/report-error", token, {"message": message[:500]})
    except RuntimeError as e:
        print(f"(also failed to report this error back to the API: {e})", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch from FinTS and print what would be imported, but never call the API to stage or report anything",
    )
    args = parser.parse_args()

    api_url = _env("LEDGER_API_URL").rstrip("/")
    sync_token = _env("LEDGER_SYNC_TOKEN")
    fints_server = _env("FINTS_SERVER")
    fints_blz = _env("FINTS_BLZ")
    fints_login = _env("FINTS_LOGIN")
    fints_pin = _env("FINTS_PIN")
    fints_product_id = _env("FINTS_PRODUCT_ID")

    try:
        account = _api_request("GET", f"{api_url}/api/v1/ledger/sync/account", sync_token)
    except RuntimeError as e:
        print(f"Could not reach the ledger API: {e}", file=sys.stderr)
        sys.exit(1)

    iban = account["iban"]
    date_from, date_to = default_date_range(account.get("last_transaction_date"))
    print(f"{account['name']} ({iban}): fetching {date_from} .. {date_to}")

    from fints.client import FinTS3PinTanClient, NeedTANResponse
    from fints.exceptions import FinTSError

    client = FinTS3PinTanClient(fints_blz, fints_login, fints_pin, fints_server, product_id=fints_product_id)
    try:
        with client:
            sepa_accounts = client.get_sepa_accounts()
            if isinstance(sepa_accounts, NeedTANResponse):
                raise RuntimeError(
                    f"Bank requires a TAN just to list accounts ({sepa_accounts.challenge!r}) — "
                    "cannot proceed unattended, see this script's own docstring"
                )
            sepa_account = next((a for a in sepa_accounts if a.iban == iban), None)
            if sepa_account is None:
                raise RuntimeError(f"IBAN {iban} not found among this FinTS access's accounts")

            result = client.get_transactions(sepa_account, date.fromisoformat(date_from), date.fromisoformat(date_to))
            if isinstance(result, NeedTANResponse):
                raise RuntimeError(
                    f"Bank requires a TAN for this transaction pull ({result.challenge!r}, "
                    f"decoupled={result.decoupled}) — cannot proceed unattended, see this script's "
                    "own docstring for why"
                )
    except (FinTSError, RuntimeError) as e:
        message = f"{type(e).__name__}: {e}"
        print(message, file=sys.stderr)
        if not args.dry_run:
            _report_error(api_url, sync_token, message)
        sys.exit(1)

    # Reuses the app's own MT940-transaction normalization — same sign/IBAN
    # fixes as every other import path (Key Design Decision #19). This is
    # why the script must run from within this repo's own venv/checkout.
    from app.services.bank_statement import lines_from_mt940_transactions

    lines = lines_from_mt940_transactions(result)
    print(f"{len(lines)} transaction(s) fetched from the bank.")

    if args.dry_run:
        for line in lines:
            print(f"  {line.booking_date}  {line.amount:>10}  {line.purpose_text or ''}")
        print("(--dry-run: nothing was submitted or reported)")
        return

    payload = {"lines": [
        {
            "booking_date": line.booking_date.isoformat(),
            "amount": str(line.amount),
            "purpose_text": line.purpose_text,
            "counterparty_name": line.counterparty_name,
            "counterparty_iban": line.counterparty_iban,
            "bank_reference": line.bank_reference,
        }
        for line in lines
    ]}
    try:
        summary = _api_request("POST", f"{api_url}/api/v1/ledger/sync/import", sync_token, payload)
    except RuntimeError as e:
        print(f"Could not stage the fetched transactions: {e}", file=sys.stderr)
        _report_error(api_url, sync_token, str(e))
        sys.exit(1)

    print(f"Staged: {summary['new_count']} new, {summary['duplicate_count']} already-known duplicate(s).")


if __name__ == "__main__":
    main()
