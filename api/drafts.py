"""
Drafts API (Plan 3 §7 + refinement E1-E4) — the Follow-up Center's backend.

All endpoints are masked-only and NEVER decrypt: they render the tokens stored on
the AgentAction payload exactly as persisted. Decryption happens only at execution
time in api/agent.py::_execute_action, behind the approved-status gate.

  POST   /drafts/generate/{meeting_transcript_id}  trigger generation
  GET    /drafts                                   grouped-by-meeting + filters (E1)
  GET    /drafts/counts                            badge aggregate (E2)
  GET    /drafts/{action_id}/version-history       knowledge version chain (E3)
  GET    /drafts/{action_id}/audit-trail           lifecycle timeline (E4/R7)
  PATCH  /drafts/{action_id}/payload               edit a pending draft
  POST   /drafts/{action_id}/regenerate            re-generate a pending draft
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Body
from sqlalchemy import func

from database.connection import SessionLocal
from database.models import AgentAction, AuditEvent, KnowledgeItem, MeetingTranscript
from database import crud
from services.draft_generation_service import DraftGenerationService
from utils.time_utils import utcnow
from utils.logger import get_logger

logger = get_logger("api.drafts")

router = APIRouter(prefix="/drafts", tags=["Drafts"])

SEND_DRAFT_TYPES = (
    "teams_message_draft",
    "email_draft",
    "reminder_draft",
    "calendar_reminder_draft",
    "followup_suggestion_draft",
)
DRAFT_ACTION_TYPES = SEND_DRAFT_TYPES + ("clarification_needed",)
EDITABLE_FIELDS = {"subject", "body", "title", "suggestion_text", "start_hint", "duration_minutes"}


# ── DTO / grouping helpers ───────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail=f"invalid date: {s!r}")


def _action_type_for(draft_type: str) -> str:
    if draft_type in ("clarification", "clarification_needed"):
        return "clarification_needed"
    return draft_type if draft_type.endswith("_draft") else f"{draft_type}_draft"


def _group_of(action: AgentAction) -> str:
    if action.action_type == "clarification_needed":
        return "clarification_needed"
    if action.status in ("pending", "approved"):
        return "pending_approval"
    if action.status == "executed":
        return "executed"
    if action.status == "failed":
        return "failed"
    return "dismissed"


def _targets(payload: dict) -> list:
    out = [payload.get("recipient_token"), payload.get("person_token")]
    out.extend(payload.get("attendee_tokens") or [])
    return [t for t in out if t]


def _preview(payload: dict) -> str:
    for key in ("body", "suggestion_text", "title", "subject"):
        if payload.get(key):
            return payload[key]
    return payload.get("display_title") or ""


def _draft_dto(a: AgentAction) -> dict:
    p = a.payload or {}
    targets = _targets(p)
    hist = p.get("edit_history") or []
    last_modified = None
    if hist and isinstance(hist[-1], dict):
        last_modified = hist[-1].get("at")
    return {
        "action_id": a.id,
        "action_type": a.action_type,
        "draft_type": p.get("draft_type"),
        "status": a.status,
        "display_title": p.get("display_title"),
        "preview": _preview(p),
        "confidence": p.get("confidence"),
        "conflict_flag": p.get("conflict_flag", False),
        "version": p.get("version"),
        "citations": p.get("citations") or [],
        "execution_target": targets[0] if targets else None,
        "knowledge_item_id": p.get("knowledge_item_id"),
        "knowledge_key": p.get("knowledge_key"),
        "reason": p.get("reason"),
        # Pass 2 P1/P3: fixed system sender for the card "From:" line + reasoning
        # projection (rationale arrives in P11/PH-9; rendered "if available").
        "sender_identity": p.get("sender_identity"),
        "rationale": p.get("rationale"),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "last_modified": last_modified,
        "edit_count": len(hist),
        "approved_at": a.approved_at.isoformat() if a.approved_at else None,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/generate/{meeting_transcript_id}")
def generate_drafts(meeting_transcript_id: int):
    db = SessionLocal()
    try:
        actions = DraftGenerationService.generate_for_meeting(meeting_transcript_id, db)
        return {
            "meeting_transcript_id": meeting_transcript_id,
            "generated": len(actions),
            "action_ids": [a.id for a in actions],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        db.close()


@router.get("")
def list_drafts(
    meeting_id: Optional[int] = None,
    draft_type: Optional[str] = None,
    status: Optional[str] = None,
    owner_token: Optional[str] = None,
    min_confidence: Optional[float] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 500,
):
    """Full lifecycle, grouped by originating meeting → status group. Server-side
    column filters (status/draft_type/date); payload filters (owner/confidence)
    applied in Python; free-text title search is left to the client (R4)."""
    db = SessionLocal()
    try:
        q = db.query(AgentAction).filter(AgentAction.action_type.in_(DRAFT_ACTION_TYPES))
        if status:
            q = q.filter(AgentAction.status == status)
        if draft_type:
            q = q.filter(AgentAction.action_type == _action_type_for(draft_type))
        if date_from:
            q = q.filter(AgentAction.created_at >= _parse_dt(date_from))
        if date_to:
            q = q.filter(AgentAction.created_at <= _parse_dt(date_to))
        actions = q.order_by(AgentAction.created_at.desc()).limit(limit).all()

        # payload-level filters (single-tenant scale — Python is fine)
        def _keep(a: AgentAction) -> bool:
            p = a.payload or {}
            if owner_token and owner_token not in _targets(p):
                return False
            if min_confidence is not None and (p.get("confidence") or 0) < min_confidence:
                return False
            return True

        actions = [a for a in actions if _keep(a)]

        # batch-resolve meeting linkage: action → KnowledgeItem.source_id → MeetingTranscript
        ki_ids = {(a.payload or {}).get("knowledge_item_id") for a in actions}
        ki_ids.discard(None)
        ki_source = {}
        if ki_ids:
            for row in db.query(KnowledgeItem.id, KnowledgeItem.source_id).filter(KnowledgeItem.id.in_(ki_ids)).all():
                ki_source[row.id] = row.source_id
        source_ids = {s for s in ki_source.values() if s}
        mt_by_source = {}
        if source_ids:
            for mt in db.query(MeetingTranscript).filter(MeetingTranscript.source_id.in_(source_ids)).all():
                mt_by_source[mt.source_id] = mt

        meetings: dict = {}
        ungrouped: list = []
        for a in actions:
            p = a.payload or {}
            source_id = ki_source.get(p.get("knowledge_item_id"))
            mt = mt_by_source.get(source_id) if source_id else None
            if meeting_id is not None and (mt is None or mt.id != meeting_id):
                continue
            dto = _draft_dto(a)
            if mt is None:
                ungrouped.append(dto)
                continue
            if mt.id not in meetings:
                meetings[mt.id] = {
                    "meeting_transcript_id": mt.id,
                    "source_id": mt.source_id,
                    "subject": mt.subject_masked,
                    "meeting_start": mt.meeting_start.isoformat() if mt.meeting_start else None,
                    "participant_tokens": mt.participant_tokens or [],
                    "groups": {"pending_approval": [], "clarification_needed": [], "failed": [], "executed": [], "dismissed": []},
                    "counts": {"pending": 0, "clarification": 0, "failed": 0, "executed": 0, "total": 0},
                }
            m = meetings[mt.id]
            grp = _group_of(a)
            m["groups"][grp].append(dto)
            m["counts"]["total"] += 1
            if grp == "pending_approval":
                m["counts"]["pending"] += 1
            elif grp == "clarification_needed":
                m["counts"]["clarification"] += 1
            elif grp == "failed":
                m["counts"]["failed"] += 1
            elif grp == "executed":
                m["counts"]["executed"] += 1

        return {"meetings": list(meetings.values()), "ungrouped": ungrouped}
    finally:
        db.close()


@router.get("/counts")
def draft_counts():
    """Lightweight badge aggregate — counts only, no payloads (E2)."""
    db = SessionLocal()
    try:
        pending = db.query(func.count(AgentAction.id)).filter(
            AgentAction.action_type.in_(SEND_DRAFT_TYPES),
            AgentAction.status == "pending",
        ).scalar() or 0
        clarification = db.query(func.count(AgentAction.id)).filter(
            AgentAction.action_type == "clarification_needed",
            AgentAction.status == "pending",
        ).scalar() or 0
        failed = db.query(func.count(AgentAction.id)).filter(
            AgentAction.action_type.in_(DRAFT_ACTION_TYPES),
            AgentAction.status == "failed",
        ).scalar() or 0
        return {
            "pending": int(pending),
            "clarification": int(clarification),
            "failed": int(failed),
            "total": int(pending) + int(clarification) + int(failed),
        }
    finally:
        db.close()


@router.get("/{action_id}/version-history")
def version_history(action_id: int):
    db = SessionLocal()
    try:
        action = db.query(AgentAction).filter(AgentAction.id == action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail="Draft not found")
        kkey = (action.payload or {}).get("knowledge_key")
        if not kkey:
            return {"action_id": action_id, "knowledge_key": None, "versions": []}
        rows = crud.list_all_versions_by_key(db, kkey, user_id=action.user_id or 1)
        return {
            "action_id": action_id,
            "knowledge_key": kkey,
            "versions": [
                {
                    "id": r.id,
                    "version": r.version,
                    "is_active": r.is_active,
                    "title_masked": r.title_masked,
                    "confidence": float(r.confidence) if r.confidence is not None else None,
                    "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                    "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                    "resolution_method": r.resolution_method,
                    "superseded_by_id": r.superseded_by_id,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.get("/{action_id}/audit-trail")
def audit_trail(action_id: int):
    db = SessionLocal()
    try:
        action = db.query(AgentAction).filter(AgentAction.id == action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail="Draft not found")
        events = crud.list_audit_for_action(db, action_id)
        return {
            "action_id": action_id,
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "actor": e.actor,
                    "outcome": e.outcome,
                    "event_data": e.event_data or {},
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
        }
    finally:
        db.close()


@router.patch("/{action_id}/payload")
def edit_draft(action_id: int, updates: dict = Body(...)):
    db = SessionLocal()
    try:
        action = db.query(AgentAction).filter(AgentAction.id == action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail="Draft not found")
        if action.status != "pending":
            raise HTTPException(status_code=409, detail=f"Draft is {action.status} — only pending drafts can be edited")

        payload = dict(action.payload or {})
        applied = {k: v for k, v in (updates or {}).items() if k in EDITABLE_FIELDS}
        if not applied:
            raise HTTPException(status_code=400, detail=f"No editable fields. Allowed: {sorted(EDITABLE_FIELDS)}")

        payload.update(applied)
        history = list(payload.get("edit_history") or [])
        history.append({"at": utcnow().isoformat(), "action": "edit", "fields": sorted(applied.keys())})
        payload["edit_history"] = history
        action.payload = payload  # reassign so SQLAlchemy tracks the JSON change

        db.add(AuditEvent(
            event_type="DRAFT_EDITED",
            actor="user",
            resource_type="AgentAction",
            resource_id=action.id,
            outcome="SUCCESS",
            event_data={"fields": sorted(applied.keys())},
        ))
        db.commit()
        return {"action_id": action_id, "edited_fields": sorted(applied.keys()), "payload": payload}
    finally:
        db.close()


@router.post("/{action_id}/regenerate")
def regenerate_draft(action_id: int, body: Optional[dict] = Body(default=None)):
    db = SessionLocal()
    try:
        edit_instructions = (body or {}).get("edit_instructions") if body else None
        action = DraftGenerationService.regenerate(action_id, db, edit_instructions=edit_instructions)
        return {"action_id": action.id, "status": action.status, "payload": action.payload}
    except ValueError as e:
        msg = str(e)
        code = 409 if "not pending" in msg else (404 if "not found" in msg else 400)
        raise HTTPException(status_code=code, detail=msg)
    finally:
        db.close()
