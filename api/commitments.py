from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.connection import SessionLocal
from database.models import Commitment

router = APIRouter(prefix="/commitments", tags=["Commitments"])

_VALID_STATUSES = {"pending", "drafted", "done", "dismissed"}


class StatusUpdate(BaseModel):
    status: str


@router.get("/")
def get_commitments(limit: int = 200):
    db = SessionLocal()
    try:
        rows = (
            db.query(Commitment)
            .order_by(Commitment.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": c.id,
                "raw_text": c.raw_text,
                "action": c.action,
                "person": c.person,
                "commitment_type": c.commitment_type,
                "status": c.status,
                "due_date": c.due_date.isoformat() if c.due_date else None,
                "source_id": c.source_id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ]
    finally:
        db.close()


@router.patch("/{commitment_id}/status")
def update_status(commitment_id: int, body: StatusUpdate):
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{body.status}'. Must be one of: {sorted(_VALID_STATUSES)}",
        )
    db = SessionLocal()
    try:
        c = db.query(Commitment).filter(Commitment.id == commitment_id).first()
        if not c:
            raise HTTPException(status_code=404, detail="Commitment not found")
        c.status = body.status
        db.commit()
        return {"id": c.id, "status": c.status}
    finally:
        db.close()
