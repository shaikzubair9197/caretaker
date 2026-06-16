from fastapi import APIRouter

from database.connection import SessionLocal
from database.models import LLMCallLog

router = APIRouter(prefix="/llm", tags=["LLM Audit"])


@router.get("/audit")
def get_audit_log(limit: int = 100):
    db = SessionLocal()
    try:
        rows = (
            db.query(LLMCallLog)
            .order_by(LLMCallLog.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "model": r.model,
                "call_type": r.call_type,
                "prompt_sha256": r.prompt_sha256,
                "entity_types": r.entity_types or [],
                "injection_warnings": r.injection_warnings or [],
                "duration_ms": r.duration_ms,
                "status": r.status,
                "fallback_reason": r.fallback_reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()
