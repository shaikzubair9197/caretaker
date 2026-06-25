from datetime import timedelta

from fastapi import APIRouter

from database.connection import SessionLocal
from database.models import SourceItem, Task, Memory, Commitment, AgentAction, LLMCallLog, ActiveWindow
from services.llm_service import preflight_check, LLMStatus, LLM_PROVIDER
from utils.time_utils import utcnow

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/detailed")
def health_detailed():
    db = SessionLocal()
    try:
        # ── Database counts ───────────────────────────────────────────────
        db_ok = True
        counts = {}
        last_panic = None
        last_telemetry = None
        last_successful_llm = None
        fallback_activations_today = 0
        try:
            counts["tasks"] = db.query(Task).count()
            counts["memories"] = db.query(Memory).count()
            counts["commitments"] = db.query(Commitment).count()
            counts["pending_actions"] = (
                db.query(AgentAction).filter(AgentAction.status == "pending").count()
            )
            counts["source_items"] = db.query(SourceItem).count()
            counts["llm_calls"] = db.query(LLMCallLog).count()

            last_src = (
                db.query(SourceItem)
                .filter(SourceItem.source_type == "panic_dump")
                .order_by(SourceItem.id.desc())
                .first()
            )
            if last_src and last_src.created_at:
                last_panic = last_src.created_at.isoformat()

            last_win = (
                db.query(ActiveWindow)
                .order_by(ActiveWindow.id.desc())
                .first()
            )
            if last_win and last_win.started_at:
                last_telemetry = last_win.started_at.isoformat()

            llm_24h = (
                db.query(LLMCallLog)
                .filter(LLMCallLog.created_at >= utcnow() - timedelta(hours=24))
                .count()
            )
            counts["llm_calls_24h"] = llm_24h

            # Last successful LLM call timestamp
            last_ok = (
                db.query(LLMCallLog)
                .filter(LLMCallLog.status == LLMStatus.SUCCESS)
                .order_by(LLMCallLog.created_at.desc())
                .first()
            )
            if last_ok and last_ok.created_at:
                last_successful_llm = last_ok.created_at.isoformat()

            # Fallback activations today (any non-SUCCESS status today)
            today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            fallback_activations_today = (
                db.query(LLMCallLog)
                .filter(
                    LLMCallLog.created_at >= today_start,
                    LLMCallLog.status != LLMStatus.SUCCESS,
                )
                .count()
            )
        except Exception as e:
            db_ok = False
            counts["error"] = str(e)

        # ── LLM pre-flight ───────────────────────────────────────────────
        pf = preflight_check()
        llm_status = "ok" if pf["azure_configured"] and pf["endpoint_configured"] and pf["api_version_configured"] else "config_missing"
        llm_diagnostics = {
            "azure_configured":         pf["azure_configured"],
            "configured_model":         pf["configured_model"],
            "endpoint_configured":      pf["endpoint_configured"],
            "api_version_configured":    pf["api_version_configured"],
            "preflight_ms":             pf["duration_ms"],
            "error":                    pf.get("error"),
        }

        return {
            "api": "ok",
            "database": "ok" if db_ok else "error",
            "llm_provider": LLM_PROVIDER,
            "llm_status": llm_status,
            "llm_diagnostics": llm_diagnostics,
            "counts": counts,
            "last_panic_dump":           last_panic,
            "last_telemetry":            last_telemetry,
            "last_successful_llm_call":  last_successful_llm,
            "fallback_activations_today": fallback_activations_today,
        }
    finally:
        db.close()
