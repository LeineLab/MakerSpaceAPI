import json
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.deps import require_admin_user
from app.database import get_db
from app.models.audit import AuditLog

router = APIRouter()


class AuditLogResponse(BaseModel):
    id: int
    actor: str
    action: str
    target_type: Optional[str]
    target_id: Optional[str]
    detail: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


def filtered_audit_query(
    db: Session,
    actor: Optional[str],
    action: Optional[str],
    target_type: Optional[str],
    target_id: Optional[str],
    q: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    forced_prefix: Optional[str] = None,
):
    """Shared by the admin log and the ledger-scoped log (which forces `ledger.`)."""
    query = db.query(AuditLog)
    if forced_prefix:
        query = query.filter(AuditLog.action.like(f"{forced_prefix}%"))
    if actor:
        query = query.filter(AuditLog.actor.ilike(f"%{actor}%"))
    if action:
        query = query.filter(AuditLog.action.like(f"{action}%"))
    if target_type:
        query = query.filter(AuditLog.target_type == target_type)
    if target_id:
        query = query.filter(AuditLog.target_id == target_id)
    if q:
        query = query.filter(AuditLog.detail.ilike(f"%{q}%"))
    if date_from:
        query = query.filter(AuditLog.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    return query


@router.get("", response_model=list[AuditLogResponse])
def list_audit_log(
    response: Response,
    actor: Optional[str] = None,
    action: Optional[str] = Query(default=None, description="Action prefix, e.g. 'machine.' or 'machine.authorization_'"),
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    q: Optional[str] = Query(default=None, description="Substring of the detail text"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Administrative action log, newest first (admin only). `X-Total-Count` carries the match count."""
    query = filtered_audit_query(db, actor, action, target_type, target_id, q, date_from, date_to)
    response.headers["X-Total-Count"] = str(query.count())
    return query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(offset).limit(limit).all()
