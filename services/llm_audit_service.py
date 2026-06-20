import hashlib
from typing import Optional

from sqlalchemy.orm import Session

from database.models import LLMCallLog
from utils.logger import get_logger

logger = get_logger("services.llm_audit")


class LLMAuditService:

    @staticmethod
    def log(
        model:              str,
        call_type:          str,
        prompt:             str,
        entity_types:       Optional[list]  = None,
        injection_warnings: Optional[list]  = None,
        duration_ms:        Optional[int]   = None,
        status:             str             = "SUCCESS",
        fallback_reason:    Optional[str]   = None,
        # ── Layer 6: exception capture ────────────────────────────────────
        exception_type:     Optional[str]   = None,
        exception_message:  Optional[str]   = None,
        # ── Layer 4: request audit ────────────────────────────────────────
        preflight_data:     Optional[dict]  = None,
        prompt_preview:     Optional[str]   = None,
        prompt_size_chars:  Optional[int]   = None,
        # ── Extraction metadata (meeting intelligence and similar) ────────
        prompt_version:     Optional[str]   = None,
        extraction_version: Optional[str]   = None,
        source_id:          Optional[int]   = None,
    ) -> Optional[int]:
        """
        Write one audit record for an LLM call using an independent DB session.

        Independent session guarantees:
        - A caller rollback does NOT erase the audit entry.
        - An audit failure does NOT rollback the caller's transaction.

        Returns the new LLMCallLog.id (or None if the write failed), so callers
        that need to link other rows back to this specific call (e.g.
        KnowledgeItem.llm_call_log_id) can do so without a second query.
        """
        from database.connection import SessionLocal

        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        # Truncate exception message to avoid unbounded column growth
        if exception_message and len(exception_message) > 800:
            exception_message = exception_message[:797] + "…"

        audit_db = SessionLocal()
        try:
            row = LLMCallLog(
                model              = model,
                call_type          = call_type,
                prompt_sha256      = prompt_hash,
                entity_types       = entity_types or [],
                injection_warnings = injection_warnings or [],
                duration_ms        = duration_ms,
                status             = status,
                fallback_reason    = fallback_reason,
                exception_type     = exception_type,
                exception_message  = exception_message,
                preflight_data     = preflight_data,
                prompt_preview     = prompt_preview,
                prompt_size_chars  = prompt_size_chars,
                prompt_version     = prompt_version,
                extraction_version = extraction_version,
                source_id          = source_id,
            )
            audit_db.add(row)
            audit_db.commit()
            logger.info(
                f"LLM audit — call_type={call_type} model={model} "
                f"status={status} duration_ms={duration_ms} "
                f"exc={exception_type or '-'}"
            )
            return row.id
        except Exception as e:
            audit_db.rollback()
            logger.error(f"Failed to write LLM audit log: {e}")
            return None
        finally:
            audit_db.close()

    @staticmethod
    def get_recent(db: Session, limit: int = 20) -> list:
        return (
            db.query(LLMCallLog)
            .order_by(LLMCallLog.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def find_successful_call(db: Session, source_id: int, call_type: str) -> Optional[LLMCallLog]:
        """Most recent successful call of `call_type` for a given source — used for idempotency checks."""
        return (
            db.query(LLMCallLog)
            .filter(
                LLMCallLog.source_id == source_id,
                LLMCallLog.call_type == call_type,
                LLMCallLog.status == "SUCCESS",
            )
            .order_by(LLMCallLog.created_at.desc())
            .first()
        )
