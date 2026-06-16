from fastapi import APIRouter, HTTPException

from database.connection import SessionLocal
from utils.time_utils import utcnow
from database.models import AgentAction

from services.brain_service import BrainService
from services.intent_service import IntentService
from services.action_service import ActionService
from utils.logger import get_logger

logger = get_logger("api.agent")

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.get("/tick")
def agent_tick():
    db = SessionLocal()
    try:
        brain = BrainService.build_context(db)
        intent = IntentService.decide(brain)
        actions = ActionService.execute(intent["actions"])
        return {
            "brain": brain,
            "intent": intent,
            "executed": actions,
            "context": brain["context"],
        }
    finally:
        db.close()


@router.post("/idle")
def agent_idle():
    """
    Called by the idle tracker daemon when the user has been inactive.
    Surfaces pending commitments as a reminder AgentAction.
    """
    db = SessionLocal()
    try:
        brain = BrainService.build_context(db)
        pending_tasks = [t for t in brain["tasks"] if t["status"] == "pending"]
        high_prio = [t for t in pending_tasks if t.get("priority") == "high"]

        payload = {
            "pending_task_count": len(pending_tasks),
            "high_priority_tasks": high_prio[:3],
            "triggered_by": "idle",
        }

        action = AgentAction(
            action_type="commitment_reminder",
            payload=payload,
            status="pending",
        )
        db.add(action)
        db.commit()

        logger.info(f"Idle event: created reminder action id={action.id}, pending={len(pending_tasks)}")
        return {
            "action_id": action.id,
            "pending_task_count": len(pending_tasks),
            "message": "Idle reminder queued",
        }
    finally:
        db.close()


@router.get("/actions")
def get_all_actions(limit: int = 100):
    """Return all AgentActions ordered newest-first."""
    db = SessionLocal()
    try:
        actions = (
            db.query(AgentAction)
            .order_by(AgentAction.created_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "count": len(actions),
            "actions": [
                {
                    "id": a.id,
                    "action_type": a.action_type,
                    "payload": a.payload,
                    "status": a.status,
                    "approved_at": a.approved_at.isoformat() if a.approved_at else None,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in actions
            ],
        }
    finally:
        db.close()


@router.get("/actions/pending")
def get_pending_actions():
    """Return all AgentActions awaiting user approval."""
    db = SessionLocal()
    try:
        actions = (
            db.query(AgentAction)
            .filter(AgentAction.status == "pending")
            .order_by(AgentAction.created_at.desc())
            .all()
        )
        return {
            "count": len(actions),
            "actions": [
                {
                    "id": a.id,
                    "action_type": a.action_type,
                    "payload": a.payload,
                    "status": a.status,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in actions
            ],
        }
    finally:
        db.close()


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: int):
    """
    Approve and execute a pending AgentAction.

    Phase 1 execution: UI-surface actions (suggest_next_task, commitment_reminder,
    focus_recommendation) are marked executed immediately — they surface data to the
    caller's response. Destructive actions (send_email, etc.) remain as BLOCKER stubs
    until Phase 2 connectors are wired.
    """
    db = SessionLocal()
    try:
        action = db.query(AgentAction).filter(AgentAction.id == action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")

        if action.status not in ("pending",):
            raise HTTPException(
                status_code=409,
                detail=f"Action is already {action.status} — cannot approve again",
            )

        action.approved_at = utcnow()
        execution_result = _execute_action(action)
        action.status = "executed" if execution_result["success"] else "failed"
        db.commit()

        logger.info(
            f"Action {action_id} approved and {action.status}: "
            f"type={action.action_type}"
        )
        return {
            "action_id": action_id,
            "status": action.status,
            "execution": execution_result,
        }
    finally:
        db.close()


def _execute_action(action: AgentAction) -> dict:
    """
    Phase 1 action executor.

    UI-surface actions: executed immediately (data returned to caller).
    Destructive actions: stubbed — log intent, mark as needing Phase 2 connector.
    """
    atype = action.action_type
    payload = action.payload or {}

    # ── UI surface actions (Phase 1 complete) ────────────────────────────────
    if atype in ("suggest_next_task", "commitment_reminder",
                 "focus_recommendation", "health_check", "panic_mode"):
        logger.info(f"Executing surface action: {atype} → {payload}")
        return {
            "success": True,
            "action_type": atype,
            "surfaced": payload,
            "note": "Displayed to user via polling client",
        }

    # ── Destructive actions (Phase 2 stubs) ──────────────────────────────────
    if atype in ("send_email", "create_calendar_event",
                 "delete_task", "external_api_call"):
        logger.warning(
            f"Action {atype} approved but Phase 2 connector not yet implemented. "
            f"Payload recorded for when connector is wired."
        )
        return {
            "success": True,
            "action_type": atype,
            "note": "Connector not yet implemented — recorded for Phase 2",
            "payload": payload,
        }

    # ── Unknown action type ───────────────────────────────────────────────────
    logger.error(f"Unknown action type: {atype}")
    return {"success": False, "action_type": atype, "note": "Unknown action type"}


@router.post("/actions/{action_id}/dismiss")
def dismiss_action(action_id: int):
    """Dismiss an AgentAction without executing it."""
    db = SessionLocal()
    try:
        action = db.query(AgentAction).filter(AgentAction.id == action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")

        action.status = "dismissed"
        db.commit()

        logger.info(f"Action {action_id} dismissed")
        return {"action_id": action_id, "status": "dismissed"}
    finally:
        db.close()