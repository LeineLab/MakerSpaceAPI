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
