from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from database.models import Commitment
from utils.config import settings
from utils.time_utils import utcnow


class CommitmentService:

    @staticmethod
    def create(
        db: Session,
        raw_text: str,
        action: str,
        person: str | None = None,
        commitment_type: str = "task",
        source_id: int | None = None,
        due_date: datetime | None = None,
        remind_offset_minutes: int | None = None,
    ) -> Commitment:
        """
        Stage a Commitment in the current session.  No commit is issued here —
        the caller owns the transaction boundary.  This ensures that commitment
        creation inside panic_dump (which batches Tasks + Memory in one atomic
        commit) cannot produce partial writes.

        Reminder auto-scheduling (Phase 6): when a `due_date` is supplied it is
        snapshotted onto the Commitment together with a derived reminder window
        (remind_at = due_date - offset, default REMINDER_DEFAULT_OFFSET_MINUTES).
        From then on the reminder daemon works ONLY from the commitment's own
        columns — it never reads back the originating KnowledgeItem. With no
        due_date the reminder fields stay NULL (no reminder scheduled).
        """
        remind_at = None
        stored_offset = None
        if due_date is not None:
            stored_offset = (
                settings.REMINDER_DEFAULT_OFFSET_MINUTES
                if remind_offset_minutes is None
                else remind_offset_minutes
            )
            remind_at = due_date - timedelta(minutes=stored_offset)

        commitment = Commitment(
            raw_text=raw_text,
            action=action,
            person=person,
            commitment_type=commitment_type,
            source_id=source_id,
            due_date=due_date,
            remind_at=remind_at,
            remind_offset_minutes=stored_offset,
        )
        db.add(commitment)
        return commitment

    # ── Reminder scheduling (Phase 6) — commitments table is the source of truth ──

    @staticmethod
    def get_due(db: Session, now: datetime | None = None) -> list[Commitment]:
        """Reminders that are due and have not fired yet: remind_at <= now AND
        reminded_at IS NULL. Cancelled/completed commitments are excluded so a
        dismissed item never pings. Reads only the commitments table."""
        now = now or utcnow()
        return (
            db.query(Commitment)
            .filter(
                Commitment.remind_at.isnot(None),
                Commitment.remind_at <= now,
                Commitment.reminded_at.is_(None),
                Commitment.status.notin_(["dismissed", "done"]),
            )
            .order_by(Commitment.remind_at.asc())
            .all()
        )

    @staticmethod
    def mark_reminded(db: Session, commitment_id: int, when: datetime | None = None) -> Commitment | None:
        """Fire-once stamp. Returns the commitment, or None if it does not exist.
        Caller owns the commit."""
        c = db.query(Commitment).filter(Commitment.id == commitment_id).first()
        if c is None:
            return None
        c.reminded_at = when or utcnow()
        return c

    @staticmethod
    def reschedule_reminder(
        db: Session,
        commitment_id: int,
        remind_at: datetime | None = None,
        remind_offset_minutes: int | None = None,
    ) -> Commitment | None:
        """Adjust a reminder and re-arm it (clears reminded_at so it can fire again).
        An explicit remind_at wins; otherwise a new offset re-derives remind_at from
        the snapshotted due_date. Returns the commitment, or None if not found.
        Caller owns the commit."""
        c = db.query(Commitment).filter(Commitment.id == commitment_id).first()
        if c is None:
            return None
        if remind_offset_minutes is not None:
            c.remind_offset_minutes = remind_offset_minutes
            if c.due_date is not None:
                c.remind_at = c.due_date - timedelta(minutes=remind_offset_minutes)
        if remind_at is not None:
            c.remind_at = remind_at
        c.reminded_at = None   # re-arm so the new schedule can fire
        return c

    @staticmethod
    def get_pending(db: Session) -> list[Commitment]:
        return (
            db.query(Commitment)
            .filter(Commitment.status == "pending")
            .order_by(Commitment.id.desc())
            .all()
        )

    @staticmethod
    def get_all(db: Session) -> list[Commitment]:
        return (
            db.query(Commitment)
            .order_by(Commitment.id.desc())
            .all()
        )
