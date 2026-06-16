from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database.models import Memory
from embeddings.embedder import encode


class MemoryService:

    @staticmethod
    def create(
        db: Session,
        text: str,
        memory_type: str = "general",
        importance: int = 1,
        source_id: int = None,
        user_id: int = 1,
    ):
        """
        Safe memory creation with dedup.

        Uses a savepoint (BEGIN NESTED) so an IntegrityError on the unique-text
        constraint only rolls back the memory insert — it does NOT roll back any
        Tasks, Commitments, or SourceItem already staged in the caller's
        transaction.  The caller is responsible for the final db.commit().
        """
        normalized_text = text.strip()

        existing = (
            db.query(Memory)
            .filter(
                Memory.user_id == user_id,
                Memory.text == normalized_text,
            )
            .first()
        )
        if existing:
            return existing

        memory = Memory(
            text=normalized_text,
            type=memory_type,
            importance=importance,
            source_id=source_id,
            user_id=user_id,
            embedding=encode(normalized_text),
        )

        try:
            with db.begin_nested():   # SAVEPOINT — only this rolls back on duplicate
                db.add(memory)
                db.flush()
        except IntegrityError:
            # Another concurrent insert beat us.  Outer transaction is intact.
            return (
                db.query(Memory)
                .filter(
                    Memory.user_id == user_id,
                    Memory.text == normalized_text,
                )
                .first()
            )

        return memory
        # NOTE: no db.commit() here — caller owns the transaction boundary.

    @staticmethod
    def get_all(db: Session):

        return (
            db.query(Memory)
            .order_by(Memory.id.desc())
            .all()
        )

    @staticmethod
    def get_recent(db: Session, limit: int = 10):

        return (
            db.query(Memory)
            .order_by(Memory.id.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def search(db: Session, keyword: str):

        return (
            db.query(Memory)
            .filter(Memory.text.ilike(f"%{keyword}%"))   # ✅ FIXED
            .order_by(Memory.id.desc())
            .all()
        )