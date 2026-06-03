from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database.models import Memory


class MemoryService:

    @staticmethod
    def create(
        db: Session,
        text: str,
        memory_type: str = "general",
        importance: int = 1
    ):
        """
        Safe memory creation:
        - prevents duplicates
        - handles race conditions
        """

        normalized_text = text.strip()

        # Step 1: check existing memory
        existing = (
            db.query(Memory)
            .filter(Memory.text == normalized_text)   # ✅ FIXED
            .first()
        )

        if existing:
            return existing

        try:
            memory = Memory(
                text=normalized_text,       # ✅ FIXED
                type=memory_type,           # ✅ FIXED
                importance=importance
            )

            db.add(memory)
            db.commit()
            db.refresh(memory)

            return memory

        except IntegrityError:
            db.rollback()

            return (
                db.query(Memory)
                .filter(Memory.text == normalized_text)   # ✅ FIXED
                .first()
            )

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