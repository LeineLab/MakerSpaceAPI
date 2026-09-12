"""Thin read-only client for Paperless-ngx document search.

Uses httpx — already a hard transitive dependency of
authlib.integrations.starlette_client (OIDC), so there's no dependency cost
to using it here too instead of maintaining a second, stdlib-based HTTP call
pattern. Paperless-ngx stays the sole place documents are stored/managed; we
only ever reference a document id.
"""
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


def list_documents(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """List Paperless documents, newest first, for the Belege overview.

    Optionally restricted to `PAPERLESS_DOCUMENT_TYPE_IDS` (e.g. only
    "Rechnung"/"Beleg", excluding other document types Paperless might also
    hold) via the same `document_type__id__in` filter Paperless's own web UI
    uses. Returns `(documents, total_count)` — `total_count` is Paperless's
    own reported count for the filtered set, matching this app's own
    `X-Total-Count` paging convention (see `GET /ledger/entries` etc.).

    Returns `([], 0)` if Paperless isn't configured or is unreachable — same
    fail-open convention as `search_documents()`, since a down/misconfigured
    Paperless must never block the rest of the ledger from working.
    """
    if not is_configured():
        return [], 0

    url = settings.PAPERLESS_URL.rstrip("/") + "/api/documents/"
    params: dict = {
        "page": offset // limit + 1,
        "page_size": limit,
        "ordering": "-created",
    }
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
        return [], 0

    documents = [
        {
            "id": doc["id"],
            "title": doc.get("title") or f"Dokument {doc['id']}",
            "created": doc.get("created"),
        }
        for doc in data.get("results", [])
    ]
    return documents, data.get("count", 0)
