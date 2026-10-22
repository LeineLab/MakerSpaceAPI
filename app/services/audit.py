import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def audit(
    db: Session,
    actor: Optional[str],
    action: str,
    target_type: Optional[str] = None,
    target_id: Any = None,
    **detail: Any,
) -> None:
    """Record an administrative action in audit_log.

    Only adds the row to the session; the caller's own commit stores it in the
    same transaction as the change itself, so a change can never be saved
    without its log entry (or the other way round).
    """
    db.add(AuditLog(
        actor=actor or "unknown",
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=json.dumps(detail, default=str, ensure_ascii=False) if detail else None,
    ))


def changes(before: dict, after: dict) -> dict:
    """{field: {"from": old, "to": new}} for the fields that differ."""
    return {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
