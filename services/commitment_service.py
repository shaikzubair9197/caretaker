from sqlalchemy.orm import Session

from database.models import Commitment


class CommitmentService:

    @staticmethod
    def create(
        db: Session,
        raw_text: str,
        action: str,
        person: str | None = None,
        commitment_type: str = "task",
        source_id: int | None = None,
    ) -> Commitment:
        """
        Stage a Commitment in the current session.  No commit is issued here —
        the caller owns the transaction boundary.  This ensures that commitment
        creation inside panic_dump (which batches Tasks + Memory in one atomic
        commit) cannot produce partial writes.
        """
        commitment = Commitment(
            raw_text=raw_text,
            action=action,
            person=person,
            commitment_type=commitment_type,
            source_id=source_id,
        )
        db.add(commitment)
        return commitment

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
