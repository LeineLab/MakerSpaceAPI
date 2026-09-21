"""Thin read-only client for Paperless-ngx document search.

Uses httpx — already a hard transitive dependency of
authlib.integrations.starlette_client (OIDC), so there's no dependency cost
to using it here too instead of maintaining a second, stdlib-based HTTP call
pattern. Paperless-ngx stays the sole place documents are stored/managed; we
only ever reference a document id.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.config import settings

_TIMEOUT_SECONDS = 5.0


def is_configured() -> bool:
    return bool(settings.PAPERLESS_URL and settings.PAPERLESS_API_TOKEN)


def search_documents(query: str, limit: int = 10) -> list[dict]:
    """Search Paperless for documents matching `query`.

    Returns an empty list if Paperless isn't configured or is unreachable —
    a down/misconfigured Paperless must never block booking a ledger entry.
    """
    if not is_configured():
        return []

    url = settings.PAPERLESS_URL.rstrip("/") + "/api/documents/"
    try:
        response = httpx.get(
            url,
            params={"query": query, "page_size": limit},
            headers={
                "Authorization": f"Token {settings.PAPERLESS_API_TOKEN}",
                "Accept": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    return [
        {
            "id": doc["id"],
            "title": doc.get("title") or f"Dokument {doc['id']}",
            "created": doc.get("created"),
        }
        for doc in data.get("results", [])
    ]


def _document_type_ids() -> list[str]:
    raw = settings.PAPERLESS_DOCUMENT_TYPE_IDS.strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


def list_documents(q: str | None = None, limit: int = 500) -> list[dict]:
    """List Paperless documents, newest first, for the Belege overview.

    Optionally restricted to `PAPERLESS_DOCUMENT_TYPE_IDS` (e.g. only
    "Rechnung"/"Beleg", excluding other document types Paperless might also
    hold) via the same `document_type__id__in` filter Paperless's own web UI
    uses, and/or `q` (Paperless's own full-text search, the same `query`
    param `search_documents()` uses — it searches document content, not just
    the title, which this app can't replicate on its own).

    Unlike `search_documents()`, this fetches up to `limit` documents in one
    request rather than paginating against Paperless — the caller
    (`GET /ledger/paperless/documents`) needs the *entire* matching set
    up front to filter by its own computed "linked" status before applying
    its own offset/limit, so Paperless-side paging wouldn't help here. 500
    is a practical bound, not a hard guarantee of completeness — plenty for
    a Verein-scale document set restricted to a couple of Dokumenttypen, but
    a Paperless instance with more matching documents than that would only
    ever show the newest 500 here.

    Returns `[]` if Paperless isn't configured or is unreachable — same
    fail-open convention as `search_documents()`, since a down/misconfigured
    Paperless must never block the rest of the ledger from working.
    """
    if not is_configured():
        return []

    url = settings.PAPERLESS_URL.rstrip("/") + "/api/documents/"
    params: dict = {
        "page_size": limit,
        "ordering": "-created",
    }
    if q:
        params["query"] = q
    type_ids = _document_type_ids()
    if type_ids:
        params["document_type__id__in"] = ",".join(type_ids)
    try:
        response = httpx.get(
            url,
            params=params,
            headers={
                "Authorization": f"Token {settings.PAPERLESS_API_TOKEN}",
                "Accept": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    return [
        {
            "id": doc["id"],
            "title": doc.get("title") or f"Dokument {doc['id']}",
            "created": doc.get("created"),
        }
        for doc in data.get("results", [])
    ]


_SUGGESTION_FETCH_LIMIT = 200


def _parse_amount_value(value) -> Decimal | None:
    """Best-effort parse of a Paperless custom field's raw value into a
    Decimal invoice amount. A Monetary custom field is commonly stored as a
    currency-prefixed string (e.g. "EUR123.45"); a Float field as a plain
    number — neither shape is documented as stable across Paperless
    versions, so this strips everything but digits/'.'/'-' rather than
    assuming one exact format."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _document_date(doc: dict) -> date | None:
    created = doc.get("created")
    if not created:
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def suggest_documents(amount: Decimal, target_date: date, limit: int = 3) -> list[dict]:
    """Suggest up to `limit` Paperless documents likely to be the receipt for
    one booking line (Key Design Decision #65), ranked by:
      1. An exact match against PAPERLESS_AMOUNT_CUSTOM_FIELD_ID, if
         configured — always ranked above any date-only match, regardless of
         how far its own document date is from `target_date`, since a
         matching invoice total is a far stronger signal than mere
         proximity.
      2. Ascending distance between the document's own `created` date and
         `target_date` (closest first) — the tiebreaker among amount
         matches, and the only signal used at all when no amount field is
         configured.

    Fetches a bounded, NOT date-windowed candidate set (unlike a hypothetical
    "documents near this date" query) specifically so a genuine amount match
    can't be missed just because the invoice was raised long before it was
    finally paid — the precedence above only matters once every candidate
    already in hand has been ranked.

    Returns `[]` if Paperless isn't configured, unreachable, or `limit <= 0`
    — same fail-open convention as search_documents()/list_documents()."""
    if not is_configured() or limit <= 0:
        return []

    url = settings.PAPERLESS_URL.rstrip("/") + "/api/documents/"
    params: dict = {"page_size": _SUGGESTION_FETCH_LIMIT, "ordering": "-created"}
    type_ids = _document_type_ids()
    if type_ids:
        params["document_type__id__in"] = ",".join(type_ids)
    try:
        response = httpx.get(
            url,
            params=params,
            headers={
                "Authorization": f"Token {settings.PAPERLESS_API_TOKEN}",
                "Accept": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    field_id_raw = settings.PAPERLESS_AMOUNT_CUSTOM_FIELD_ID.strip()
    field_id = int(field_id_raw) if field_id_raw.isdigit() else None
    target_amount = abs(amount)

    ranked = []
    for doc in data.get("results", []):
        doc_date = _document_date(doc)
        distance = abs((doc_date - target_date).days) if doc_date else 10**9
        amount_match = False
        if field_id is not None:
            for cf in doc.get("custom_fields") or []:
                if cf.get("field") == field_id:
                    parsed = _parse_amount_value(cf.get("value"))
                    amount_match = parsed is not None and parsed == target_amount
                    break
        ranked.append((not amount_match, distance, doc))

    ranked.sort(key=lambda t: (t[0], t[1]))
    return [
        {
            "id": doc["id"],
            "title": doc.get("title") or f"Dokument {doc['id']}",
            "created": doc.get("created"),
            "amount_match": not is_no_match,
        }
        for is_no_match, _, doc in ranked[:limit]
    ]
