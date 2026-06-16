from fastapi import APIRouter

from database.connection import SessionLocal
from database.models import Memory

router = APIRouter(prefix="/memories", tags=["Memories"])


@router.get("/")
def get_memories(limit: int = 200):
    db = SessionLocal()
    try:
        rows = (
            db.query(Memory)
            .order_by(Memory.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": m.id,
                "text": m.text,
                "type": m.type,
                "importance": m.importance,
                "source_id": m.source_id,
                "has_embedding": m.embedding is not None,
                "embedding_dims": len(m.embedding) if m.embedding else 0,
            }
            for m in rows
        ]
    finally:
        db.close()
