"""
DraftGenerationService — turns a meeting's open KnowledgeItems into reviewable,
masked AgentAction drafts (Plan 3 §2, refinement R9).

Orchestration only — reuses, never reimplements:
  - Plan 2's knowledge_retrieval_service.retrieve() for the confidence/conflict
    gate (the floor + conflict detection already run inside retrieve()).
  - Phase 4's LLMService.generate_draft() for content (provider-agnostic).
  - The Phase 3 executor payload contract (recipient_token / person_token /
    attendee_tokens / start_hint / title / subject / body + knowledge_item_id).

No masking step (inherited for free — KnowledgeItem columns are masked by
construction per Plan 1) and NO decryption (drafts stay masked until execution).
The masked routing tokens are derived deterministically from the item's
owner_token; only their *existence* is checked against the vault (never decrypted).
"""

from typing import Optional

from sqlalchemy.orm import Session

from database.models import (
    AgentAction,
    AuditEvent,
    KnowledgeItem,
    MeetingTranscript,
    VaultToken,
)
from services.llm_service import LLMService
from services import knowledge_retrieval_service
from utils.config import settings
from utils.time_utils import utcnow
from utils.logger import get_logger

logger = get_logger("services.draft_generation")

_DRAFT_ACTION_TYPES = (
    "teams_message_draft",
    "email_draft",
    "reminder_draft",
    "calendar_reminder_draft",
    "followup_suggestion_draft",
    "clarification_needed",
)


# ── Routing helpers ──────────────────────────────────────────────────────────

def _vault_has(db: Session, token_name: Optional[str]) -> bool:
    """Existence check for a vault token by NAME only — never reads ciphertext,
    never decrypts. Used to decide whether an owner is reachable by email/Teams."""
    if not token_name:
        return False
    return db.query(VaultToken.id).filter(VaultToken.token == token_name).first() is not None


def _classify(item: KnowledgeItem, db: Session):
    """
    Deterministic knowledge_type → (draft_type, action_type, has_email, has_aad).
    Returns None for non-draftable types (decision, information_request,
    mentioned_*, github_pr, jira_ticket, system_reference, credential_reference).
    """
    kt = item.knowledge_type
    owner = item.owner_token
    has_email = _vault_has(db, f"{owner}_EMAIL") if owner else False
    has_aad = _vault_has(db, f"{owner}_AAD") if owner else False

    if kt == "deadline" and item.due_at:
        return ("calendar_reminder", "calendar_reminder_draft", has_email, has_aad)
    if kt in ("action_item", "question", "follow_up") and (has_aad or has_email):
        if has_aad:
            return ("teams_message", "teams_message_draft", has_email, has_aad)
        return ("email", "email_draft", has_email, has_aad)
    if kt == "commitment":
        if has_email:
            return ("email", "email_draft", has_email, has_aad)
        return ("reminder", "reminder_draft", has_email, has_aad)
    if kt in ("risk", "blocker", "follow_up"):
        return ("followup_suggestion", "followup_suggestion_draft", has_email, has_aad)
    return None


def _build_context(item: KnowledgeItem) -> dict:
    """Masked-only LLM context, built straight from KnowledgeItem columns."""
    return {
        "knowledge_type": item.knowledge_type,
        "title": item.title_masked,
        "detail": item.detail_masked,
        "owner_token": item.owner_token,
        "due_hint": item.due_at.isoformat() if item.due_at else None,
    }


def _verify_citations(data: dict, results: list) -> bool:
    """
    Structural anti-hallucination backstop (Plan 3 §2): every value_ref the model
    echoed must be in the closed set that was sent in. Any citation outside that
    set → reject the draft. The model cannot reference a fact it was never given.
    """
    allowed = {r.get("value_ref") for r in results if isinstance(r, dict) and r.get("value_ref")}
    cites = data.get("citations")
    if cites is None:
        return True  # nothing claimed — confidence gate already passed upstream
    if not isinstance(cites, list):
        return False
    for c in cites:
        vr = c.get("value_ref") if isinstance(c, dict) else None
        if vr is not None and vr not in allowed:
            return False
    return True


def _shape_payload(draft_type, action_type, item, data, results, confidence, conflict_flag, has_email, has_aad) -> dict:
    """Build the AgentAction.payload exactly as the Phase 3 executor expects,
    carrying masked routing tokens + the §3 review metadata (citations,
    knowledge_item_id, confidence, conflict_flag, version, edit_history)."""
    owner = item.owner_token
    base = {
        "knowledge_item_id": item.id,
        "knowledge_key": item.knowledge_key,
        "version": item.version,
        "draft_type": draft_type,
        # Permanent sender identity (Pass 2 — Phase 1). Fixed system constant,
        # never inferred. The execution layer re-validates against this before
        # any send; the UI renders it as the draft's "From:" line. Recipients
        # vary (recipient_token, masked); the sender never does.
        "sender_identity": settings.SENDER_IDENTITY,
        "confidence": float(confidence) if confidence is not None else None,
        "conflict_flag": conflict_flag,
        "citations": data.get("citations") or [],
        "edit_history": [],
    }
    if draft_type == "teams_message":
        base.update({
            "recipient_token": f"{owner}_AAD" if has_aad else (f"{owner}_EMAIL" if has_email else None),
            "body": data.get("body", ""),
            "display_title": item.title_masked,
        })
    elif draft_type == "email":
        base.update({
            "recipient_token": f"{owner}_EMAIL" if has_email else None,
            "subject": data.get("subject", ""),
            "body": data.get("body", ""),
            "display_title": data.get("subject") or item.title_masked,
        })
    elif draft_type == "reminder":
        base.update({
            "title": data.get("title", item.title_masked),
            "person_token": owner,
            "display_title": data.get("title") or item.title_masked,
        })
    elif draft_type == "calendar_reminder":
        base.update({
            "title": data.get("title", item.title_masked),
            "start_hint": item.due_at.isoformat() if item.due_at else None,
            "duration_minutes": 30,
            "attendee_tokens": [f"{owner}_EMAIL"] if has_email else [],
            "display_title": data.get("title") or item.title_masked,
        })
    elif draft_type == "followup_suggestion":
        base.update({
            "suggestion_text": data.get("suggestion_text", ""),
            "display_title": item.title_masked,
        })
    return base


def _audit(db: Session, event_type: str, item: KnowledgeItem, source_id: Optional[int], extra: dict) -> None:
    """Inline audit write (committed by the caller, same pattern as
    transcript_ingestion_service)."""
    db.add(AuditEvent(
        event_type=event_type,
        actor="system",
        source_id=source_id,
        resource_type="KnowledgeItem",
        resource_id=item.id,
        outcome="ERROR" if event_type == "DRAFT_FAILED" else "SUCCESS",
        event_data={"knowledge_type": item.knowledge_type, **(extra or {})},
    ))


class DraftGenerationService:

    @staticmethod
    def generate_for_meeting(meeting_transcript_id: int, db: Session) -> list:
        """
        Generate masked, pending AgentAction drafts for every draftable open
        KnowledgeItem of a meeting. Idempotent across re-runs: items that already
        have a non-dismissed draft are skipped (no duplicates).
        """
        transcript = (
            db.query(MeetingTranscript)
            .filter(MeetingTranscript.id == meeting_transcript_id)
            .first()
        )
        if transcript is None:
            raise ValueError(f"MeetingTranscript {meeting_transcript_id} not found")

        items = (
            db.query(KnowledgeItem)
            .filter(
                KnowledgeItem.source_id == transcript.source_id,
                KnowledgeItem.is_active.is_(True),
                KnowledgeItem.status == "open",
            )
            .all()
        )

        # Dedup guard: knowledge_item_ids that already have a live draft.
        existing = (
            db.query(AgentAction)
            .filter(
                AgentAction.action_type.in_(_DRAFT_ACTION_TYPES),
                AgentAction.status != "dismissed",
            )
            .all()
        )
        already = {(a.payload or {}).get("knowledge_item_id") for a in existing}

        created: list = []
        for item in items:
            if item.id in already:
                continue
            classified = _classify(item, db)
            if classified is None:
                continue
            action = DraftGenerationService._process_item(item, classified, transcript, db)
            if action is not None:
                created.append(action)

        logger.info(
            f"DraftGenerationService: generated {len(created)} draft(s) for "
            f"transcript {meeting_transcript_id}"
        )
        return created

    @staticmethod
    def _process_item(item: KnowledgeItem, classified, transcript: MeetingTranscript, db: Session) -> Optional[AgentAction]:
        draft_type, action_type, has_email, has_aad = classified
        user_id = item.user_id or 1
        source_id = transcript.source_id
        query = f"{item.title_masked or ''} {item.knowledge_key or ''}".strip()

        # ── Confidence/conflict gate — reuses Plan 2 retrieve() (R9) ─────────
        retrieval = knowledge_retrieval_service.retrieve(query, db, user_id=user_id)
        if retrieval.get("status") == "insufficient_confidence":
            return DraftGenerationService._make_clarification(item, source_id, db, reason="insufficient_confidence")

        results = retrieval.get("results", [])
        conflict_flag = bool(retrieval.get("conflict"))
        confidence = results[0]["confidence"] if results else None
        _audit(db, "DRAFT_GATE_DECISION", item, source_id,
               {"decision": "proceed", "draft_type": draft_type, "conflict": conflict_flag})

        # ── LLM draft (masked context only) ──────────────────────────────────
        llm = LLMService.generate_draft(draft_type, _build_context(item), results, source_id=source_id)
        if not llm.succeeded or not llm.data:
            _audit(db, "DRAFT_FAILED", item, source_id, {"reason": f"llm_{llm.status}", "draft_type": draft_type})
            db.commit()
            return None

        # ── Structural citation verification ─────────────────────────────────
        if not _verify_citations(llm.data, results):
            _audit(db, "DRAFT_FAILED", item, source_id, {"reason": "unverifiable_citation", "draft_type": draft_type})
            db.commit()
            return None

        payload = _shape_payload(draft_type, action_type, item, llm.data, results,
                                 confidence, conflict_flag, has_email, has_aad)
        action = AgentAction(action_type=action_type, payload=payload, status="pending", user_id=user_id)
        db.add(action)
        db.flush()
        _audit(db, "DRAFT_GENERATED", item, source_id, {"action_id": action.id, "draft_type": draft_type})
        db.commit()
        return action

    @staticmethod
    def _make_clarification(item: KnowledgeItem, source_id: Optional[int], db: Session, reason: str) -> AgentAction:
        payload = {
            "knowledge_item_id": item.id,
            "knowledge_key": item.knowledge_key,
            "version": item.version,
            "draft_type": "clarification",
            "sender_identity": settings.SENDER_IDENTITY,
            "display_title": item.title_masked,
            "reason": reason,
            "edit_history": [],
        }
        action = AgentAction(action_type="clarification_needed", payload=payload, status="pending", user_id=item.user_id or 1)
        db.add(action)
        db.flush()
        _audit(db, "DRAFT_GATE_DECISION", item, source_id,
               {"decision": "clarification", "reason": reason, "action_id": action.id})
        db.commit()
        return action

    @staticmethod
    def regenerate(agent_action_id: int, db: Session, edit_instructions: Optional[str] = None) -> AgentAction:
        """
        Re-fetch retrieval fresh and overwrite a pending draft's payload in place,
        appending to its append-only edit_history. 409-style guard via ValueError
        (the API layer maps it) if the action is no longer pending.
        """
        action = db.query(AgentAction).filter(AgentAction.id == agent_action_id).first()
        if action is None:
            raise ValueError(f"AgentAction {agent_action_id} not found")
        if action.status != "pending":
            raise ValueError(f"AgentAction {agent_action_id} is '{action.status}', not pending — cannot regenerate")

        prev_payload = action.payload or {}
        ki_id = prev_payload.get("knowledge_item_id")
        item = db.query(KnowledgeItem).filter(KnowledgeItem.id == ki_id).first() if ki_id else None
        if item is None:
            raise ValueError(f"originating knowledge_item {ki_id} not found for action {agent_action_id}")

        classified = _classify(item, db)
        if classified is None:
            raise ValueError(f"knowledge_item {ki_id} is no longer draftable")
        draft_type, action_type, has_email, has_aad = classified
        source_id = item.source_id
        user_id = item.user_id or 1

        query = f"{item.title_masked or ''} {item.knowledge_key or ''}".strip()
        retrieval = knowledge_retrieval_service.retrieve(query, db, user_id=user_id)
        results = retrieval.get("results", [])
        conflict_flag = bool(retrieval.get("conflict"))
        confidence = results[0]["confidence"] if results else None

        ctx = _build_context(item)
        if edit_instructions:
            ctx["edit_instructions"] = edit_instructions

        llm = LLMService.generate_draft(draft_type, ctx, results, source_id=source_id)
        if not llm.succeeded or not llm.data or not _verify_citations(llm.data, results):
            reason = "unverifiable_citation" if (llm.succeeded and llm.data) else f"llm_{llm.status}"
            _audit(db, "DRAFT_FAILED", item, source_id, {"reason": reason, "action_id": action.id, "phase": "regenerate"})
            db.commit()
            raise ValueError(f"regeneration failed to produce a verifiable draft ({reason})")

        new_payload = _shape_payload(draft_type, action_type, item, llm.data, results,
                                     confidence, conflict_flag, has_email, has_aad)
        history = list(prev_payload.get("edit_history") or [])
        history.append({
            "at": utcnow().isoformat(),
            "action": "regenerate",
            "instructions": (edit_instructions or "")[:200],
        })
        new_payload["edit_history"] = history
        action.payload = new_payload
        action.action_type = action_type  # type may shift if owner reachability changed
        db.add(AuditEvent(
            event_type="DRAFT_EDITED",
            actor="user",
            resource_type="AgentAction",
            resource_id=action.id,
            outcome="SUCCESS",
            event_data={"action": "regenerate", "draft_type": draft_type},
        ))
        db.commit()
        logger.info(f"DraftGenerationService: regenerated draft for action {agent_action_id}")
        return action
