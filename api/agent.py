import re
from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from utils.time_utils import utcnow
from database.models import AgentAction, AuditEvent, KnowledgeItem

from services.brain_service import BrainService
from services.intent_service import IntentService
from services.action_service import ActionService
from services.vault_service import VaultService
from services import secure_store_service
from services.commitment_service import CommitmentService
from services.graph.senders.teams_sender import TeamsSender
from services.graph.senders.email_sender import EmailSender
from services.graph.senders.calendar_writer import CalendarWriter
from services.graph.senders.errors import GraphSendError
from utils.config import settings
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
        # Row lock (H1 double-execute guard): two concurrent approves serialize
        # here, so the status check below rejects the loser with 409 instead of
        # both proceeding to execute and double-sending.
        action = (
            db.query(AgentAction)
            .filter(AgentAction.id == action_id)
            .with_for_update()
            .first()
        )
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")

        if action.status not in ("pending",):
            raise HTTPException(
                status_code=409,
                detail=f"Action is already {action.status} — cannot approve again",
            )

        execution_result = _do_approve(action, db)
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


def _do_approve(action: AgentAction, db: Session) -> dict:
    """
    Shared approve+execute path (status-gate fix + ACTION_APPROVED audit), used by
    both the single approve endpoint and approve-batch. Assumes `action` is already
    loaded, row-locked, and pending.

    Durably marks "approved" and commits BEFORE execution: VaultService.decrypt()
    requires status == "approved", so without this commit recipient-token
    decryption is unreachable. It also makes partial-failure forensics correct —
    if execution crashes, the row durably shows "approved", not stuck at "pending".
    """
    action.approved_at = utcnow()
    action.status = "approved"
    db.add(AuditEvent(
        event_type="ACTION_APPROVED",
        actor="user",
        resource_type="AgentAction",
        resource_id=action.id,
        outcome="SUCCESS",
        event_data={"action_type": action.action_type},
    ))
    db.commit()

    execution_result = _execute_action(action, db)
    action.status = "executed" if execution_result["success"] else "failed"
    db.commit()
    return execution_result


def _execute_action(action: AgentAction, db: Session) -> dict:
    """
    Action executor.

    UI-surface actions: executed immediately (data returned to caller).
    Destructive actions: stubbed — log intent, mark as needing Phase 2 connector.

    `db` is threaded through for downstream draft-execution branches
    (teams_message_draft / email_draft / calendar_reminder_draft / reminder_draft),
    which decrypt recipient tokens via VaultService.decrypt(..., db=db). Existing
    surface/stub branches do not use it.
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

    # ── Meeting follow-up draft execution (Plan 3) ───────────────────────────
    # Each branch decrypts only the tokens it needs (now reachable thanks to the
    # status-gate fix above), substitutes them in memory for the Graph call, and
    # audits ACTION_EXECUTED / ACTION_FAILED with a specific reason. Nothing is
    # persisted or logged in plaintext.
    if atype == "teams_message_draft":
        return _execute_teams_message_draft(action, payload, db)
    if atype == "email_draft":
        return _execute_email_draft(action, payload, db)
    if atype == "calendar_reminder_draft":
        return _execute_calendar_reminder_draft(action, payload, db)
    if atype == "reminder_draft":
        return _execute_reminder_draft(action, payload, db)
    if atype in ("followup_suggestion_draft", "clarification_needed"):
        # Surface-only — no external side effect. Still audited as executed.
        return _audit_executed(action, db, {"note": "surface-only, no send performed"})

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


# ── Draft execution helpers (Plan 3) ─────────────────────────────────────────
# Inline masked tokens look like <S42_PERSON_1> / <S42_SPEAKER_2_EMAIL> — the
# source-scoped indexed tokens produced by transcript_ingestion_service. The
# vault stores them without the angle brackets.
_BODY_TOKEN_RE = re.compile(r"<(S\d+_[A-Z0-9_]+)>")
# Phase 4: inline credential placeholders, e.g. {{SECURE_REF:1}}. A draft may carry
# several independent refs; each maps via payload.secure_refs to a credential_key.
_SECURE_REF_RE = re.compile(r"\{\{SECURE_REF:(\d+)\}\}")


def _rehydrate(text: str, db: Session, action_id: int, justification: str,
               secure_refs: Optional[dict] = None) -> str:
    """
    Substitute inline placeholders with decrypted plaintext, IN MEMORY ONLY, for
    the duration of the send — never persisted, never logged.

      • <Sn_TOKEN>        → vault PII/routing value (VaultService.decrypt).
      • {{SECURE_REF:n}}  → the credential's LATEST ACTIVE value, resolved per
                            reference via secure_store_service.decrypt_credential
                            (rotation-safe: a post-draft rotation is used here with
                            no regeneration). Each ref resolves INDEPENDENTLY.

    A token with no vault entry / a ref with no resolved credential is left masked
    (logged) rather than aborting the whole send; a genuine gate failure
    (PermissionError) propagates to the caller's try/except and becomes ACTION_FAILED.
    At send time the action is already 'approved', so the decrypt gate is satisfied.
    """
    if not text:
        return text

    # Pass 1 — vault routing tokens (unchanged behaviour).
    resolved: dict[str, str] = {}

    def _sub(match):
        name = match.group(1)
        if name not in resolved:
            try:
                resolved[name] = VaultService.decrypt(name, justification, "system", action_id, db)
            except KeyError:
                logger.warning(f"_rehydrate: token absent from vault for action {action_id} — left masked")
                return match.group(0)
        return resolved[name]

    text = _BODY_TOKEN_RE.sub(_sub, text)

    # Pass 2 — secure credential references (Phase 4), each resolved independently.
    if secure_refs:
        resolved_refs: dict[str, str] = {}

        def _sub_ref(match):
            n = match.group(1)
            if n not in resolved_refs:
                credential_key = (secure_refs.get(n) or {}).get("credential_key")
                if not credential_key:
                    logger.warning(f"_rehydrate: secure ref {n} unresolved for action {action_id} — left masked")
                    return match.group(0)
                try:
                    resolved_refs[n] = secure_store_service.decrypt_credential(
                        credential_key, justification, "system", action_id, db
                    )
                except KeyError:
                    logger.warning(f"_rehydrate: credential for secure ref {n} not found for action {action_id} — left masked")
                    return match.group(0)
            return resolved_refs[n]

        text = _SECURE_REF_RE.sub(_sub_ref, text)

    return text


def _stale_reason(payload: dict, db: Session):
    """
    H2 stale-citation guard: a draft is stale if its originating KnowledgeItem
    was superseded (is_active=False) between generation and execution. Returns a
    reason string if stale, else None.
    """
    ki_id = payload.get("knowledge_item_id")
    if not ki_id:
        return None
    item = db.query(KnowledgeItem).filter(KnowledgeItem.id == ki_id).first()
    if item is None:
        return f"knowledge_item {ki_id} not found"
    if item.is_active is False:
        return f"knowledge_item {ki_id} superseded to version {item.version}"
    return None


def _audit_executed(action: AgentAction, db: Session, extra: dict) -> dict:
    db.add(AuditEvent(
        event_type="ACTION_EXECUTED",
        actor="system",
        resource_type="AgentAction",
        resource_id=action.id,
        outcome="SUCCESS",
        event_data={"action_type": action.action_type, **(extra or {})},
    ))
    db.commit()
    logger.info(f"Action {action.id} ({action.action_type}) executed")
    return {"success": True, "action_type": action.action_type, **(extra or {})}


def _audit_failed(action: AgentAction, db: Session, reason: str, message: str) -> dict:
    db.add(AuditEvent(
        event_type="ACTION_FAILED",
        actor="system",
        resource_type="AgentAction",
        resource_id=action.id,
        outcome="ERROR",
        event_data={"action_type": action.action_type, "reason": reason, "message": (message or "")[:200]},
    ))
    db.commit()
    logger.warning(f"Action {action.id} ({action.action_type}) failed: {reason}")
    return {"success": False, "action_type": action.action_type, "reason": reason, "note": (message or "")[:200]}


def _canonical_sender_or_fail(action: AgentAction, payload: dict, db: Session):
    """
    Pass 2 — Phase 1: Permanent Sender Identity guard.

    Every outbound Graph action must originate from the one fixed system identity
    `settings.SENDER_IDENTITY` (care.taker@amperatech.ai) — never an inferred one.
    Returns (canonical_sender_upn, None) to proceed, or (None, failure_dict) when
    the identity is missing or a draft was stamped with a different sender; the
    failure path audits ACTION_FAILED reason="sender_identity_violation" and
    performs no send. Recipients stay dynamic; only the sender is validated here.
    """
    canonical = (settings.SENDER_IDENTITY or "").strip()
    if not canonical:
        return None, _audit_failed(action, db, "sender_identity_violation",
                                   "SENDER_IDENTITY is not configured")
    stamped = payload.get("sender_identity")
    if stamped is not None and stamped != canonical:
        return None, _audit_failed(action, db, "sender_identity_violation",
                                   f"draft sender_identity does not match the canonical system identity")
    return canonical, None


def _is_service_account(address: Optional[str]) -> bool:
    """True when a resolved recipient address is the Caretaker service account
    (settings.SENDER_IDENTITY). Send-time backstop for the routing invariant: the
    assistant/organizer is never a recipient. Compares against the CONFIGURED
    identity, not a hardcoded literal."""
    canonical = (settings.SENDER_IDENTITY or "").strip().lower()
    return bool(canonical) and (address or "").strip().lower() == canonical


def _execute_teams_message_draft(action: AgentAction, payload: dict, db: Session) -> dict:
    stale = _stale_reason(payload, db)
    if stale:
        return _audit_failed(action, db, "stale_knowledge", stale)
    recipient_token = payload.get("recipient_token")
    if not recipient_token:
        return _audit_failed(action, db, "missing_recipient", "payload has no recipient_token")
    sender_upn, fail = _canonical_sender_or_fail(action, payload, db)
    if fail:
        return fail
    try:
        recipient_id = VaultService.decrypt(recipient_token, "execute_teams_message_draft", "system", action.id, db)
        body_html = _rehydrate(payload.get("body", ""), db, action.id, "execute_teams_message_draft", payload.get("secure_refs"))
        sender = TeamsSender(upn=sender_upn)
        chat_id = sender.resolve_one_on_one_chat(recipient_id)
        result = sender.send_message(chat_id, body_html)
        return _audit_executed(action, db, {"graph_message_id": result.get("message_id")})
    except GraphSendError as e:
        return _audit_failed(action, db, e.reason, str(e))
    except (PermissionError, KeyError) as e:
        return _audit_failed(action, db, "vault_error", str(e))
    except Exception as e:                       # every failure has a specific, audited outcome
        return _audit_failed(action, db, "unknown_error", str(e))


def _execute_email_draft(action: AgentAction, payload: dict, db: Session) -> dict:
    stale = _stale_reason(payload, db)
    if stale:
        return _audit_failed(action, db, "stale_knowledge", stale)
    recipient_token = payload.get("recipient_token")
    if not recipient_token:
        return _audit_failed(action, db, "missing_recipient", "payload has no recipient_token")
    sender_upn, fail = _canonical_sender_or_fail(action, payload, db)
    if fail:
        return fail
    try:
        to_email = VaultService.decrypt(recipient_token, "execute_email_draft", "system", action.id, db)
        # Hard invariant backstop: never deliver to the Caretaker service account,
        # even if upstream routing somehow resolved to it (e.g. unresolved self_token).
        if _is_service_account(to_email):
            return _audit_failed(action, db, "recipient_is_service_account",
                                 "refusing to send: recipient resolves to the Caretaker service account")
        secure_refs = payload.get("secure_refs")
        subject = _rehydrate(payload.get("subject", ""), db, action.id, "execute_email_draft", secure_refs)
        body_html = _rehydrate(payload.get("body", ""), db, action.id, "execute_email_draft", secure_refs)
        EmailSender(upn=sender_upn).send_mail(to_email, subject, body_html)
        return _audit_executed(action, db, {"sent": True})
    except GraphSendError as e:
        return _audit_failed(action, db, e.reason, str(e))
    except (PermissionError, KeyError) as e:
        return _audit_failed(action, db, "vault_error", str(e))
    except Exception as e:
        return _audit_failed(action, db, "unknown_error", str(e))


def _execute_calendar_reminder_draft(action: AgentAction, payload: dict, db: Session) -> dict:
    stale = _stale_reason(payload, db)
    if stale:
        return _audit_failed(action, db, "stale_knowledge", stale)
    start_iso = payload.get("start_hint")
    if not start_iso:
        return _audit_failed(action, db, "missing_start", "payload has no start_hint")
    sender_upn, fail = _canonical_sender_or_fail(action, payload, db)
    if fail:
        return fail
    try:
        subject = _rehydrate(payload.get("title", ""), db, action.id, "execute_calendar_reminder_draft")
        duration = int(payload.get("duration_minutes") or 30)
        attendee_emails: list[str] = []
        for tok in (payload.get("attendee_tokens") or []):
            try:
                attendee_emails.append(VaultService.decrypt(tok, "execute_calendar_reminder_draft", "system", action.id, db))
            except KeyError:
                logger.warning(f"calendar draft: attendee token absent from vault for action {action.id} — skipped")
        result = CalendarWriter(upn=sender_upn).create_event(subject, start_iso, duration, attendee_emails)
        return _audit_executed(action, db, {"event_id": result.get("event_id")})
    except GraphSendError as e:
        return _audit_failed(action, db, e.reason, str(e))
    except (PermissionError, KeyError) as e:
        return _audit_failed(action, db, "vault_error", str(e))
    except Exception as e:
        return _audit_failed(action, db, "unknown_error", str(e))


def _execute_reminder_draft(action: AgentAction, payload: dict, db: Session) -> dict:
    # reminder_draft is a local Commitment — no Graph call (per Plan 3 §2/§4).
    stale = _stale_reason(payload, db)
    if stale:
        return _audit_failed(action, db, "stale_knowledge", stale)
    try:
        title = _rehydrate(payload.get("title", ""), db, action.id, "execute_reminder_draft")
        person = None
        person_token = payload.get("person_token")
        if person_token:
            try:
                person = VaultService.decrypt(person_token, "execute_reminder_draft", "system", action.id, db)
            except KeyError:
                person = None
        # Snapshot the deadline onto the Commitment ONCE, here at the extraction→
        # execution boundary (Phase 6). The reminder daemon thereafter works only
        # from the commitment's own columns and never reads this KnowledgeItem again.
        due_date = None
        ki_id = payload.get("knowledge_item_id")
        if ki_id:
            ki = db.query(KnowledgeItem).filter(KnowledgeItem.id == ki_id).first()
            due_date = ki.due_at if ki else None
        commitment = CommitmentService.create(
            db, raw_text=title, action=title, person=person, commitment_type="reminder",
            due_date=due_date,
        )
        db.flush()
        return _audit_executed(
            action, db,
            {"commitment_id": commitment.id, "remind_at": commitment.remind_at.isoformat() if commitment.remind_at else None},
        )
    except (PermissionError, KeyError) as e:
        return _audit_failed(action, db, "vault_error", str(e))
    except Exception as e:
        return _audit_failed(action, db, "unknown_error", str(e))


@router.post("/actions/{action_id}/dismiss")
def dismiss_action(action_id: int):
    """Dismiss an AgentAction without executing it."""
    db = SessionLocal()
    try:
        action = db.query(AgentAction).filter(AgentAction.id == action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")

        action.status = "dismissed"
        db.add(AuditEvent(
            event_type="ACTION_DISMISSED",
            actor="user",
            resource_type="AgentAction",
            resource_id=action.id,
            outcome="SUCCESS",
            event_data={"action_type": action.action_type},
        ))
        db.commit()

        logger.info(f"Action {action_id} dismissed")
        return {"action_id": action_id, "status": "dismissed"}
    finally:
        db.close()


# Draft action types the bulk "Approve All / Approve Selected" button may sweep.
# clarification_needed is deliberately excluded — it must never be auto-approved.
_BATCH_APPROVABLE = (
    "teams_message_draft",
    "email_draft",
    "reminder_draft",
    "calendar_reminder_draft",
    "followup_suggestion_draft",
)


@router.post("/actions/approve-batch")
def approve_batch(body: dict = Body(...)):
    """
    Approve (and execute) multiple pending draft actions in one call — the
    "Approve All / Approve Selected" path. Loops the SAME single-approve code
    (_do_approve), under the same row lock (H1). Silently skips ids that are not
    found, not pending, or not an approvable draft type (clarification_needed is
    never swept).
    """
    action_ids = body.get("action_ids") or []
    db = SessionLocal()
    try:
        results = []
        for aid in action_ids:
            action = (
                db.query(AgentAction)
                .filter(AgentAction.id == aid)
                .with_for_update()
                .first()
            )
            if action is None:
                results.append({"action_id": aid, "skipped": True, "reason": "not_found"})
                continue
            if action.status != "pending" or action.action_type not in _BATCH_APPROVABLE:
                results.append({
                    "action_id": aid, "skipped": True,
                    "reason": f"status={action.status},type={action.action_type}",
                })
                continue
            execution_result = _do_approve(action, db)
            results.append({"action_id": aid, "status": action.status, "execution": execution_result})

        approved = [r for r in results if not r.get("skipped")]
        logger.info(f"approve-batch: {len(approved)}/{len(action_ids)} approved")
        return {"submitted": len(action_ids), "approved": len(approved), "results": results}
    finally:
        db.close()


@router.post("/actions/dismiss-batch")
def dismiss_batch(body: dict = Body(...)):
    """
    Dismiss multiple pending actions in one call — the "Reject Selected" path.
    Symmetric to approve-batch; audits ACTION_DISMISSED per row. Silently skips
    ids that are not found or not pending. clarification_needed CAN be rejected
    here (it just isn't auto-approvable above).
    """
    action_ids = body.get("action_ids") or []
    db = SessionLocal()
    try:
        results = []
        for aid in action_ids:
            action = (
                db.query(AgentAction)
                .filter(AgentAction.id == aid)
                .with_for_update()
                .first()
            )
            if action is None:
                results.append({"action_id": aid, "skipped": True, "reason": "not_found"})
                continue
            if action.status != "pending":
                results.append({"action_id": aid, "skipped": True, "reason": f"status={action.status}"})
                continue
            action.status = "dismissed"
            db.add(AuditEvent(
                event_type="ACTION_DISMISSED",
                actor="user",
                resource_type="AgentAction",
                resource_id=action.id,
                outcome="SUCCESS",
                event_data={"action_type": action.action_type},
            ))
            db.commit()
            results.append({"action_id": aid, "status": "dismissed"})

        dismissed = [r for r in results if not r.get("skipped")]
        logger.info(f"dismiss-batch: {len(dismissed)}/{len(action_ids)} dismissed")
        return {"submitted": len(action_ids), "dismissed": len(dismissed), "results": results}
    finally:
        db.close()