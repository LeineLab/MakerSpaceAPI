from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import require_admin_user
from app.database import get_db
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionResponse

router = APIRouter()


@router.get("/{nfc_id}", response_model=list[TransactionResponse])
def get_user_transactions(
    nfc_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(require_admin_user),
    db: Session = Depends(get_db),
):
    """Full transaction history for a user (admin only)."""
    return (
        db.query(Transaction)
        .filter(Transaction.user_id == nfc_id)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
