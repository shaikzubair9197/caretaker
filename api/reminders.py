"""
Reminders API — auto-scheduled, adjustable reminders (Follow-up Center plan — Phase 6).

The reminder window is snapshotted onto each Commitment at creation
(due_date + remind_at + remind_offset_minutes), so every endpoint here — and the
meeting_scheduler daemon that calls them — works EXCLUSIVELY from the commitments
table. The extraction layer (KnowledgeItem) is never read at reminder time.

All routes sit behind the existing global X-API-Key middleware (registered in
app.py with the shared auth dependency). No vault/threat/masking involvement:
a reminder Commitment already holds local, execution-layer text.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.connection import SessionLocal
from database.models import Commitment
from services.commitment_service import CommitmentService
from utils.logger import get_logger

router = APIRouter(prefix="/reminders", tags=["Reminders"])
logger = get_logger("api.reminders")


def _dto(c: Commitment) -> dict:
    return {
        "commitment_id": c.id,
        "action": c.action,
        "person": c.person,
        "commitment_type": c.commitment_type,
        "status": c.status,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "remind_at": c.remind_at.isoformat() if c.remind_at else None,
        "remind_offset_minutes": c.remind_offset_minutes,
        "reminded_at": c.reminded_at.isoformat() if c.reminded_at else None,
    }


@router.get("/due")
def list_due_reminders() -> dict:
    """Reminders due to fire now (remind_at <= now AND not yet fired). The daemon
    polls this, fires one desktop notification per item, then POSTs /fired."""
    db = SessionLocal()
    try:
        return {"due": [_dto(c) for c in CommitmentService.get_due(db)]}
    finally:
        db.close()


class ReminderPatch(BaseModel):
    remind_at: Optional[datetime] = None
    remind_offset_minutes: Optional[int] = None


@router.patch("/{commitment_id}")
def reschedule_reminder(commitment_id: int, body: ReminderPatch) -> dict:
    """Reschedule a reminder. An explicit remind_at wins; otherwise a new offset
    re-derives remind_at from the snapshotted due_date. Re-arms the reminder
    (clears reminded_at) so the new schedule can fire."""
    if body.remind_at is None and body.remind_offset_minutes is None:
        raise HTTPException(400, "Provide remind_at and/or remind_offset_minutes.")
    db = SessionLocal()
    try:
        c = CommitmentService.reschedule_reminder(
            db, commitment_id,
            remind_at=body.remind_at,
            remind_offset_minutes=body.remind_offset_minutes,
        )
        if c is None:
            raise HTTPException(404, f"Commitment {commitment_id} not found.")
        db.commit()
        logger.info(f"Reminder rescheduled for commitment {commitment_id}")
        return _dto(c)
    finally:
        db.close()


@router.post("/{commitment_id}/fired")
def mark_reminder_fired(commitment_id: int) -> dict:
    """Fire-once stamp: mark a reminder as fired so it never repeats. Called by the
    daemon after it shows the desktop notification."""
    db = SessionLocal()
    try:
        c = CommitmentService.mark_reminded(db, commitment_id)
        if c is None:
            raise HTTPException(404, f"Commitment {commitment_id} not found.")
        db.commit()
        logger.info(f"Reminder marked fired for commitment {commitment_id}")
        return _dto(c)
    finally:
        db.close()
