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


def search_documents(query: str, limit: int = 10, target_date: date | None = None) -> list[dict]:
    """Search Paperless for documents matching `query`.

    When `target_date` is given (#68), fetches a wider candidate pool than
    `limit` and re-sorts it by ascending distance to `target_date` before
    truncating — Paperless's own relevance ordering treats every textual
    match as roughly equal (e.g. "Contabo Server" appears on every monthly
    invoice), so without this the one actually relevant to the booking being
    searched for can easily sit below several other, equally-relevant-by-text
    hits. Without `target_date`, behaves exactly as before (Paperless's own
    ordering, `limit` results fetched directly).

    Returns an empty list if Paperless isn't configured or is unreachable —
    a down/misconfigured Paperless must never block booking a ledger entry.
    """
    if not is_configured():
        return []

    fetch_size = max(limit * 5, 50) if target_date is not None else limit
    url = settings.PAPERLESS_URL.rstrip("/") + "/api/documents/"
    try:
        response = httpx.get(
            url,
            params={"query": query, "page_size": fetch_size},
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

    results = [
        {
            "id": doc["id"],
            "title": doc.get("title") or f"Dokument {doc['id']}",
            "created": doc.get("created"),
        }
        for doc in data.get("results", [])
    ]
    if target_date is not None:
        results.sort(key=lambda d: _date_distance(d, target_date))
        results = results[:limit]
    return results


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


def _date_distance(doc: dict, target_date: date) -> int:
    """Absolute day distance between a (already-simplified) doc dict's own
    date and `target_date` — a document with no parseable date sorts last,
    never wins a distance-based tiebreak."""
    doc_date = _document_date(doc)
    return abs((doc_date - target_date).days) if doc_date else 10**9


def _fetch_documents(params: dict) -> list[dict]:
    """Raw (un-simplified) Paperless document dicts for one filtered query —
    shared by suggest_documents()'s several candidate batches below. Same
    fail-open convention as every other function here: an unreachable or
    misconfigured Paperless yields an empty batch, not an exception."""
    url = settings.PAPERLESS_URL.rstrip("/") + "/api/documents/"
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
        return response.json().get("results", [])
    except (httpx.HTTPError, ValueError):
        return []


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

    Candidate fetch (#68 — was previously a single "newest 200 documents
    overall" query, which silently excluded any document older than however
    many had been added to Paperless since; a still-recent booking whose
    receipt was uploaded around the time of the transaction routinely fell
    outside that window once the Verein's Paperless instance held more than
    a couple hundred documents, so a same-day exact-date match could be
    missing from the candidates entirely while unrelated, merely-more-recent
    documents got suggested instead): two date-windowed queries around
    `target_date` — documents at-or-before it (newest-first, so the
    on-target document is always the very first row) and documents strictly
    after it (oldest-first) — so a document dated at or near `target_date`
    is always a candidate regardless of the total document count. When an
    amount field is configured, a third, NOT date-windowed "newest overall"
    batch is also fetched and merged in, preserving (on a best-effort basis
    — still bounded, not exhaustive) the "amount always wins, regardless of
    date distance" precedence above for a genuine match raised well outside
    the two date windows.

    Returns `[]` if Paperless isn't configured, unreachable, or `limit <= 0`
    — same fail-open convention as search_documents()/list_documents()."""
    if not is_configured() or limit <= 0:
        return []

    base_params: dict = {}
    type_ids = _document_type_ids()
    if type_ids:
        base_params["document_type__id__in"] = ",".join(type_ids)

    window = _SUGGESTION_FETCH_LIMIT // 2
    field_id_raw = settings.PAPERLESS_AMOUNT_CUSTOM_FIELD_ID.strip()
    field_id = int(field_id_raw) if field_id_raw.isdigit() else None

    batches = [
        _fetch_documents({
            **base_params,
            "created__date__lte": target_date.isoformat(),
            "ordering": "-created",
            "page_size": window,
        }),
        _fetch_documents({
            **base_params,
            "created__date__gt": target_date.isoformat(),
            "ordering": "created",
            "page_size": window,
        }),
    ]
    if field_id is not None:
        batches.append(_fetch_documents({**base_params, "ordering": "-created", "page_size": window}))

    seen_ids: set = set()
    candidates: list[dict] = []
    for batch in batches:
        for doc in batch:
            if doc["id"] in seen_ids:
                continue
            seen_ids.add(doc["id"])
            candidates.append(doc)

    target_amount = abs(amount)

    ranked = []
    for doc in candidates:
        distance = _date_distance(doc, target_date)
        parsed_amount = None
        if field_id is not None:
            for cf in doc.get("custom_fields") or []:
                if cf.get("field") == field_id:
                    parsed_amount = _parse_amount_value(cf.get("value"))
                    break
        amount_match = parsed_amount is not None and parsed_amount == target_amount
        ranked.append((not amount_match, distance, doc, parsed_amount, amount_match))

    ranked.sort(key=lambda t: (t[0], t[1]))
    return [
        {
            "id": doc["id"],
            "title": doc.get("title") or f"Dokument {doc['id']}",
            "created": doc.get("created"),
            "amount_match": amount_match,
            # The document's own parsed custom-field value (#66) — shown
            # instead of a separate match/no-match badge so the row stays
            # one line and doesn't grow the reserved suggestion slots.
            "amount": parsed_amount,
        }
        for _, _, doc, parsed_amount, amount_match in ranked[:limit]
    ]
