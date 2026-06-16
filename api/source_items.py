from fastapi import APIRouter

from database.connection import SessionLocal
from database.models import SourceItem, Task, Commitment, Memory

router = APIRouter(prefix="/source-items", tags=["Source Items"])


@router.get("/")
def get_source_items(limit: int = 50):
    db = SessionLocal()
    try:
        rows = (
            db.query(SourceItem)
            .order_by(SourceItem.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": s.id,
                "source_type": s.source_type,
                "raw_text": s.raw_text[:300] + ("…" if len(s.raw_text) > 300 else ""),
                "noise_removed": s.noise_removed,
                "masked_text": s.masked_text,
                "sensitivity_label": s.sensitivity_label,
                "metadata_": s.metadata_,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]
    finally:
        db.close()


@router.get("/{source_id}/children")
def get_children(source_id: int):
    db = SessionLocal()
    try:
        tasks = db.query(Task).filter(Task.source_id == source_id).all()
        commitments = db.query(Commitment).filter(Commitment.source_id == source_id).all()
        memories = db.query(Memory).filter(Memory.source_id == source_id).all()
        return {
            "source_id": source_id,
            "tasks": [
                {"id": t.id, "description": t.description, "priority": t.priority, "status": t.status}
                for t in tasks
            ],
            "commitments": [
                {"id": c.id, "action": c.action, "person": c.person, "status": c.status}
                for c in commitments
            ],
            "memories": [
                {"id": m.id, "text": m.text[:120] + ("…" if len(m.text) > 120 else ""), "type": m.type}
                for m in memories
            ],
        }
    finally:
        db.close()
