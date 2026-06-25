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

import re
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
from services import knowledge_retrieval_service, secure_store_service
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


# Explicit delivery-channel hints in the (masked) item text. The requester's stated
# channel wins over the default — "send it via Outlook" must produce an email even
# when the recipient is also reachable on Teams.
_EMAIL_CHANNEL_RE = re.compile(r"\b(outlook|e-?mail|inbox|\bmail\b)\b", re.IGNORECASE)
_TEAMS_CHANNEL_RE = re.compile(r"\b(teams|chat|direct\s+message|\bdm\b|instant\s+message)\b", re.IGNORECASE)


def _preferred_channel(item: KnowledgeItem) -> Optional[str]:
    """Explicit delivery channel requested in the masked item text ('email'/'outlook'
    → email; 'teams'/'chat' → teams), or None when unstated/ambiguous. Deterministic,
    masked-only — never an LLM call."""
    text = f"{item.title_masked or ''} {item.detail_masked or ''}"
    email = bool(_EMAIL_CHANNEL_RE.search(text))
    teams = bool(_TEAMS_CHANNEL_RE.search(text))
    if email and not teams:
        return "email"
    if teams and not email:
        return "teams"
    return None


def _classify(item: KnowledgeItem, db: Session, recipient_token: Optional[str]):
    """
    Deterministic knowledge_type → (draft_type, action_type, has_email, has_aad).
    Returns None for non-draftable types (decision, information_request,
    mentioned_*, github_pr, jira_ticket, system_reference, credential_reference).

    Reachability (has_email/has_aad) is evaluated on the RESOLVED RECIPIENT — the
    party who should receive the draft — never the owner. When Caretaker committed
    the work, that recipient is the requester; the Caretaker service account is never
    a recipient. See _resolve_recipient_token.

    Channel: an explicitly requested channel (_preferred_channel) wins; otherwise the
    default is Teams when reachable, else email.
    """
    kt = item.knowledge_type
    has_email = _vault_has(db, f"{recipient_token}_EMAIL") if recipient_token else False
    has_aad = _vault_has(db, f"{recipient_token}_AAD") if recipient_token else False

    if kt == "deadline" and item.due_at:
        return ("calendar_reminder", "calendar_reminder_draft", has_email, has_aad)
    if kt in ("action_item", "question", "follow_up") and (has_aad or has_email):
        pref = _preferred_channel(item)
        # Honour an explicitly requested channel when the recipient is reachable there.
        if pref == "email" and has_email:
            return ("email", "email_draft", has_email, has_aad)
        if pref == "teams" and has_aad:
            return ("teams_message", "teams_message_draft", has_email, has_aad)
        # No (reachable) preference → default: Teams if reachable, else email.
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


# ── Action Item vs Commitment classification (Phase 3) ───────────────────────
#
# DERIVED STATE — INVARIANT: followup_category (and its reason/counterparty) is
# NEVER permanent business data. It is recomputed from the CURRENT meeting context
# every time a draft is generated or regenerated, so changes to participants, self
# identity, or transcript corrections always re-derive the category. Callers must
# never reuse a stale cached value when the underlying context has changed.

CLASSIFICATION_VERSION = 1   # bump when the rules below change (audit/debug aid)

# Max draft attempts when a credential draft fails placeholder integrity — one
# regeneration, then fail (no partial recovery). Plain drafts get a single attempt.
_MAX_DRAFT_ATTEMPTS = 2


def _strip_tok(token):
    return token.strip("<>") if isinstance(token, str) else token


def _counterparty_is_external(counterparty, participants: set) -> bool:
    """A counterparty makes an item a Commitment when it sits OUTSIDE the meeting:
    a person token not among the participants, or any named team/department/vendor/
    customer/group (which is never a meeting-participant token)."""
    if not isinstance(counterparty, dict):
        return False
    token = _strip_tok(counterparty.get("token"))
    if token:
        return token not in participants
    return bool(counterparty.get("name"))   # named external entity


def _resolve_counterparty(counterparty, participants: set):
    """Authoritative, context-resolved counterparty for downstream consumers
    (esp. Phase 4 SecureReference resolution): {kind, token, name, is_participant},
    or None. is_participant is resolved against the CURRENT participant set."""
    if not isinstance(counterparty, dict):
        return None
    token = _strip_tok(counterparty.get("token"))
    name = counterparty.get("name")
    if not token and not name:
        return None
    return {
        "kind": counterparty.get("kind"),
        "token": token,
        "name": name,
        "is_participant": bool(token and token in participants),
    }


def _classify_followup(item: KnowledgeItem, transcript: MeetingTranscript) -> dict:
    """
    Derive the Follow-up Center category + explainability metadata from the CURRENT
    meeting context (never exposes knowledge_type). Returns:
        {"followup_category": "action_item"|"commitment",
         "classification_reason": external_counterparty|external_owner|assigned_to_self|fallback,
         "classification_version": int,
         "counterparty": {kind, token, name, is_participant} | None}
    Decision order is unchanged (external party wins first) — this only adds the
    reason + normalized counterparty alongside the existing category.
    """
    participants = {_strip_tok(t) for t in (transcript.participant_tokens or []) if t}
    self_token = _strip_tok(transcript.self_token)
    owner = _strip_tok(item.owner_token)
    raw_counterparty = (item.extra_data or {}).get("counterparty")

    if _counterparty_is_external(raw_counterparty, participants):
        category, reason = "commitment", "external_counterparty"
    elif owner and owner not in participants:
        category, reason = "commitment", "external_owner"
    elif owner and self_token and owner == self_token:
        category, reason = "action_item", "assigned_to_self"
    else:
        category, reason = "action_item", "fallback"

    return {
        "followup_category": category,
        "classification_reason": reason,
        "classification_version": CLASSIFICATION_VERSION,
        "counterparty": _resolve_counterparty(raw_counterparty, participants),
    }


def _classify_followup_category(item: KnowledgeItem, transcript: MeetingTranscript) -> str:
    """Back-compat thin wrapper returning just the category string."""
    return _classify_followup(item, transcript)["followup_category"]


# ── Recipient resolution (hard invariant: never the Caretaker service account) ────
#
# The Caretaker account (settings.SENDER_IDENTITY, captured per-meeting as
# transcript.self_token) is the assistant/organizer — it is NEVER a recipient of a
# generated draft. A draft's recipient is the human party who should receive the
# deliverable, resolved from the actual meeting participants with the self/service
# account excluded.

def _resolve_recipient_token(item: KnowledgeItem, transcript: Optional[MeetingTranscript]) -> Optional[str]:
    """
    Resolve the base participant token that should RECEIVE this draft.

    Routing:
      • Caretaker (self) is the owner/committer → the requesting counterparty.
        e.g. Caretaker saying "I'll email you the key" → send to the asker, not self.
      • otherwise                                → the owner (the external party the
        follow-up is directed at).

    HARD INVARIANT: the Caretaker self/service-account token is excluded as a
    recipient in all cases. If resolution lands on it (or no other party exists),
    the draft has no valid recipient and nothing is ever addressed to Caretaker.
    """
    owner = _strip_tok(item.owner_token)
    self_token = _strip_tok(transcript.self_token) if transcript is not None else None
    participants = (
        {_strip_tok(t) for t in (transcript.participant_tokens or []) if t}
        if transcript is not None else set()
    )
    counterparty = _resolve_counterparty((item.extra_data or {}).get("counterparty"), participants)
    cp_token = counterparty.get("token") if counterparty else None

    if owner and self_token and owner == self_token:
        # Caretaker committed → deliver to the requester. Prefer the explicitly
        # captured counterparty; otherwise infer it deterministically from the
        # participants (the requester named in the item text, or the sole other
        # party in a 1:1) so a missing counterparty never drops the draft.
        recipient = cp_token or _infer_requester_from_context(item, participants, self_token)
    else:
        recipient = owner

    # HARD INVARIANT: never address the Caretaker service account.
    if recipient is not None and recipient == self_token:
        recipient = None
    return recipient


def _infer_requester_from_context(item: KnowledgeItem, participants: set, self_token: Optional[str]) -> Optional[str]:
    """Deterministic, masked-only fallback for the requester when Caretaker committed
    but no explicit counterparty was captured by extraction. Looks at the meeting
    participants (never the Caretaker self):
      1. a participant token named in the item's masked title/detail
         (e.g. "Email the key to S5_SPEAKER_2"), else
      2. the only other participant — a 1:1 meeting has a single requester.
    Never reads the secret value, never calls an LLM."""
    others = [p for p in participants if p and p != self_token]
    if not others:
        return None
    text = f"{item.title_masked or ''} {item.detail_masked or ''}"
    named = [p for p in others if (p in text) or (f"<{p}>" in text)]
    if named:
        return named[0]            # first participant explicitly named as the target
    if len(others) == 1:
        return others[0]           # 1:1 meeting → the other party is the requester
    return None


# ── Secure reference resolution (Phase 4) ────────────────────────────────────
#
# SECURITY INVARIANT: nothing here ever touches a plaintext credential. The
# descriptor parser reads only MASKED text; resolution returns only non-secret
# metadata (credential_key / system_name / context); the value is resolved to its
# LATEST ACTIVE version exclusively at reveal/send, server-side and audited. The
# payload's secure_refs map holds placeholder→credential_key, never a value.

_SECURE_REF_TOKEN_RE = re.compile(r"\{\{SECURE_REF:(\d+)\}\}")


def _secure_ref_label(descriptor: dict) -> str:
    """Masked-safe human label for a credential descriptor (env + system + type).
    Built only from non-secret metadata — never a value."""
    env = (descriptor.get("context") or {}).get("environment")
    parts = [p for p in (env, descriptor.get("system_name"), descriptor.get("credential_type")) if p]
    return " ".join(parts) if parts else "credential"


def _candidate_dto(match: dict) -> dict:
    """Masked-safe candidate descriptor for a clarification card — labels only."""
    return {
        "credential_key": match.get("credential_key"),
        "system_name": match.get("system_name"),
        "credential_type": match.get("credential_type"),
        "context": match.get("context") or {},
        "label": _secure_ref_label(match),
    }


def _dedup_candidates(candidates: list) -> list:
    seen, out = set(), []
    for c in candidates:
        key = c.get("credential_key")
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _resolve_secure_refs(item: KnowledgeItem, db: Session, is_secret_request: bool):
    """
    Canonical Secure Reference Resolution pipeline for one draftable item.

    Returns one of:
      (None, None)                  — not a credential-bearing item (no descriptor).
      ("resolved", secure_refs)     — every reference auto-resolved at high
                                      confidence; secure_refs maps "n" → {credential_key,
                                      masked_label, ...}. The LLM emits {{SECURE_REF:n}}.
      ("clarification", info)       — ALL-OR-CLARIFICATION: any ambiguous / zero-match
                                      reference makes the whole draft a clarification card.
                                      info.reason ∈ {ambiguous_credential, no_matching_credential}.

    AUTHORITATIVE RESOLUTION FLOW (precedence — do not reorder):
      1. Descriptor extraction from MASKED text is the AUTHORITATIVE trigger for
         secure-credential resolution. `build_secure_references` finding a credential
         descriptor is what turns an item into a credential draft — robust to
         "send <name> the key" phrasing that the intent guard deliberately suppresses.
      2. Structured SecureReference matching (resolve_secure_reference: system_name +
         context dimensions + credential_type) is the PRIMARY resolution mechanism;
         literal label matching is never required.
      3. `is_secret_request` (intent detection) is ONLY a supporting/confidence
         signal — a fallback OR-trigger when the descriptor parser finds nothing. It
         is never the sole gate and never overrides the structured match.
    """
    text = f"{item.title_masked or ''} {item.detail_masked or ''}".strip()
    refs = secure_store_service.build_secure_references(text)
    if not refs and is_secret_request:
        # Intent says "secret" but no descriptor matched — fall back to a generic
        # reference (system/type unknown ⇒ will usually need clarification).
        refs = [{"credential_type": None, "system_name": None, "context": {}}]
    if not refs:
        return None, None

    # Enrich each inferred reference with the owner so the FULL structured
    # SecureReference (credential_type / system_name / context / owner) can be
    # persisted on a clarification card and reused at selection time — the
    # transcript is never re-parsed (Phase 4 refinement R5).
    owner = _strip_tok(item.owner_token)
    if owner:
        for ref in refs:
            ref["owner"] = owner

    threshold = settings.SECURE_RESOLUTION_THRESHOLD
    secure_refs: dict = {}
    ambiguous: list = []
    unmatched: list = []
    for n, ref in enumerate(refs, start=1):
        res = secure_store_service.resolve_secure_reference(ref, db)
        matches = res.get("matches") or []
        confidence = res.get("confidence") or 0.0
        if not matches:
            unmatched.append(_secure_ref_label(ref))
        elif confidence < threshold:
            # Below threshold — _confidence() already discounts near-ties between
            # the top candidates, so a multi-match ambiguity lands here too.
            ambiguous.extend(_candidate_dto(m) for m in matches)
        else:
            best = matches[0]
            secure_refs[str(n)] = {
                "credential_key": best.get("credential_key"),
                "masked_label": _secure_ref_label(best),
                "credential_type": best.get("credential_type"),
                "system_name": best.get("system_name"),
                "status": "resolved",
            }

    # All-or-clarification: ambiguity wins (pick-one), else any zero is a "no match".
    # Either way persist the inferred SecureReference(s) so selection reuses them.
    if ambiguous:
        return "clarification", {
            "reason": "ambiguous_credential",
            "candidates": _dedup_candidates(ambiguous),
            "pending_secure_refs": refs,
        }
    if unmatched:
        return "clarification", {
            "reason": "no_matching_credential",
            "candidates": [],
            "unmatched": unmatched,
            "pending_secure_refs": refs,
        }
    return "resolved", secure_refs


def _verify_secure_refs(data: dict, secure_refs: Optional[dict]) -> bool:
    """
    HARD placeholder-integrity invariant (anti-hallucination backstop). The set of
    {{SECURE_REF:n}} tokens the model emitted must form an EXACT bijection with the
    resolved secure_refs map. Specifically, reject the draft when:

      • a placeholder ID appears more than once (duplicate),
      • a placeholder has no matching secure_refs entry (orphan / out-of-range ID),
      • a secure_refs entry has no matching placeholder (missing mapping).

    There is no partial-recovery: on failure the caller REGENERATES the draft. A
    draft with no credentials (no refs, no tokens) trivially passes.
    """
    allowed = set((secure_refs or {}).keys())
    blob = " ".join(str(data.get(k) or "") for k in ("body", "subject", "suggestion_text", "title"))
    found = _SECURE_REF_TOKEN_RE.findall(blob)   # ordered list of ID strings (with repeats)

    if len(found) != len(set(found)):
        return False                              # duplicate placeholder ID
    return set(found) == allowed                  # exact bijection (no orphan, no missing)


def _build_context(item: KnowledgeItem, secure_refs: Optional[dict] = None,
                   recipient_token: Optional[str] = None) -> dict:
    """Masked-only LLM context, built straight from KnowledgeItem columns. When
    secure_refs is present, the model is given each placeholder + a masked label so
    it can place {{SECURE_REF:n}} inline — never a value (which it never receives).

    recipient_token is the person the message is ADDRESSED to (the requester when
    Caretaker committed) — the model must greet this token, NOT the owner/committer."""
    ctx = {
        "knowledge_type": item.knowledge_type,
        "title": item.title_masked,
        "detail": item.detail_masked,
        # owner_token = who committed/sends (context only — NEVER the addressee).
        "owner_token": item.owner_token,
        # recipient_token = who to greet/address. Bracketed so it's used verbatim.
        "recipient_token": f"<{recipient_token}>" if recipient_token else None,
        "due_hint": item.due_at.isoformat() if item.due_at else None,
    }
    if secure_refs:
        ctx["secure_refs"] = [
            {
                "ref": f"{{{{SECURE_REF:{k}}}}}",
                "label": v.get("masked_label"),
                "credential_type": v.get("credential_type"),
                "system_name": v.get("system_name"),
            }
            for k, v in sorted(secure_refs.items(), key=lambda kv: int(kv[0]))
        ]
    return ctx


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


def _shape_payload(draft_type, action_type, item, data, results, confidence, conflict_flag, has_email, has_aad, recipient_token) -> dict:
    """Build the AgentAction.payload exactly as the Phase 3 executor expects,
    carrying masked routing tokens + the §3 review metadata (citations,
    knowledge_item_id, confidence, conflict_flag, version, edit_history).

    recipient_token is the resolved deliverable recipient (the requester when
    Caretaker committed) — NEVER the Caretaker service account. Sent drafts
    (teams/email) and calendar attendees route to it; the local reminder keeps the
    owner as a label since it is not a sent message."""
    owner = item.owner_token
    recipient = recipient_token
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
            "recipient_token": f"{recipient}_AAD" if (recipient and has_aad) else (f"{recipient}_EMAIL" if (recipient and has_email) else None),
            "body": data.get("body", ""),
            "display_title": item.title_masked,
        })
    elif draft_type == "email":
        base.update({
            "recipient_token": f"{recipient}_EMAIL" if (recipient and has_email) else None,
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
            "attendee_tokens": [f"{recipient}_EMAIL"] if (recipient and has_email) else [],
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
            recipient_token = _resolve_recipient_token(item, transcript)
            classified = _classify(item, db, recipient_token)
            if classified is None:
                continue
            action = DraftGenerationService._process_item(item, classified, transcript, db, recipient_token)
            if action is not None:
                created.append(action)

        logger.info(
            f"DraftGenerationService: generated {len(created)} draft(s) for "
            f"transcript {meeting_transcript_id}"
        )
        return created

    @staticmethod
    def _process_item(item: KnowledgeItem, classified, transcript: MeetingTranscript, db: Session, recipient_token: Optional[str] = None) -> Optional[AgentAction]:
        draft_type, action_type, has_email, has_aad = classified
        user_id = item.user_id or 1
        source_id = transcript.source_id
        classification = _classify_followup(item, transcript)
        query = f"{item.title_masked or ''} {item.knowledge_key or ''}".strip()

        # ── Confidence/conflict gate — reuses Plan 2 retrieve() (R9) ─────────
        retrieval = knowledge_retrieval_service.retrieve(query, db, user_id=user_id)
        if retrieval.get("status") == "insufficient_confidence":
            return DraftGenerationService._make_clarification(
                item, source_id, db, reason="insufficient_confidence", classification=classification
            )

        results = retrieval.get("results", [])
        conflict_flag = bool(retrieval.get("conflict"))
        confidence = results[0]["confidence"] if results else None
        _audit(db, "DRAFT_GATE_DECISION", item, source_id,
               {"decision": "proceed", "draft_type": draft_type, "conflict": conflict_flag})

        # ── Secure reference resolution (Phase 4) ────────────────────────────
        # If this item asks for a confidential value, resolve it server-side to a
        # credential_key (never the value). All-or-clarification: anything ambiguous
        # or unmatched short-circuits to a clarification card instead of a draft.
        is_secret_request = bool((retrieval.get("intent") or {}).get("is_secret_request"))
        sec_status, sec_data = _resolve_secure_refs(item, db, is_secret_request)
        secure_refs: Optional[dict] = None
        if sec_status == "clarification":
            return DraftGenerationService._make_clarification(
                item, source_id, db, reason=sec_data["reason"], classification=classification,
                extra={
                    "candidates": sec_data.get("candidates"),
                    "unmatched": sec_data.get("unmatched"),
                    "pending_secure_refs": sec_data.get("pending_secure_refs"),
                },
            )
        if sec_status == "resolved":
            secure_refs = sec_data
            _audit(db, "SECURE_REF_RESOLVED", item, source_id,
                   {"count": len(secure_refs), "is_secret_request": is_secret_request})

        # ── LLM draft (masked context only) ──────────────────────────────────
        # Placeholder integrity is a hard invariant: if the model emits a malformed
        # {{SECURE_REF:n}} set we REGENERATE (no patch/recovery), bounded by
        # _MAX_DRAFT_ATTEMPTS. Citation/LLM failures are terminal as before.
        ctx = _build_context(item, secure_refs, recipient_token)
        max_attempts = _MAX_DRAFT_ATTEMPTS if secure_refs else 1
        llm = None
        for attempt in range(1, max_attempts + 1):
            llm = LLMService.generate_draft(draft_type, ctx, results, source_id=source_id)
            if not llm.succeeded or not llm.data:
                _audit(db, "DRAFT_FAILED", item, source_id, {"reason": f"llm_{llm.status}", "draft_type": draft_type})
                db.commit()
                return None
            if not _verify_citations(llm.data, results):
                _audit(db, "DRAFT_FAILED", item, source_id, {"reason": "unverifiable_citation", "draft_type": draft_type})
                db.commit()
                return None
            if _verify_secure_refs(llm.data, secure_refs):
                break
            # Placeholder integrity failed — log exactly what the model returned so
            # we can see whether it omitted, duplicated, or mis-formatted the placeholder.
            allowed = set((secure_refs or {}).keys())
            blob = " ".join(str(llm.data.get(k) or "") for k in ("body", "subject", "suggestion_text", "title"))
            found = _SECURE_REF_TOKEN_RE.findall(blob)
            logger.warning(
                "SECURE_REF integrity failed attempt=%s — expected=%s found=%s "
                "body_preview=%r",
                attempt, sorted(allowed), found, blob[:300],
            )
            # Placeholder integrity failed — regenerate rather than recover.
            if attempt >= max_attempts:
                _audit(db, "DRAFT_FAILED", item, source_id,
                       {"reason": "unverifiable_secure_ref", "draft_type": draft_type, "attempts": attempt})
                db.commit()
                return None
            _audit(db, "DRAFT_REGENERATED", item, source_id,
                   {"reason": "placeholder_integrity", "draft_type": draft_type, "attempt": attempt})

        payload = _shape_payload(draft_type, action_type, item, llm.data, results,
                                 confidence, conflict_flag, has_email, has_aad, recipient_token)
        payload.update(classification)   # followup_category + reason + version + counterparty
        if secure_refs:
            payload["secure_refs"] = secure_refs   # placeholder → credential_key (never a value)
        action = AgentAction(action_type=action_type, payload=payload, status="pending", user_id=user_id)
        db.add(action)
        db.flush()
        _audit(db, "DRAFT_GENERATED", item, source_id, {"action_id": action.id, "draft_type": draft_type})
        db.commit()
        return action

    @staticmethod
    def _make_clarification(item: KnowledgeItem, source_id: Optional[int], db: Session, reason: str,
                            classification: Optional[dict] = None, extra: Optional[dict] = None) -> AgentAction:
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
        if classification:
            payload.update(classification)   # followup_category + reason + version + counterparty
        if extra:
            # Phase 4: masked candidate list (pick-one) / unmatched descriptors —
            # labels only, NEVER a value. Lets the UI render the credential ask.
            payload.update({k: v for k, v in extra.items() if v is not None})
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

        # Resolve recipient up front (never the Caretaker self) so reachability and
        # the rebuilt payload both route to the correct party.
        transcript = db.query(MeetingTranscript).filter(MeetingTranscript.source_id == item.source_id).first()
        recipient_token = _resolve_recipient_token(item, transcript)
        classified = _classify(item, db, recipient_token)
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

        # Re-resolve secure references against the CURRENT store (Phase 4). Only an
        # all-resolved result attaches placeholders; ambiguous/zero degrades to prose
        # (no {{SECURE_REF}} tokens) rather than turning a regenerate into a
        # clarification. Rotations are still picked up at reveal/send (latest active).
        is_secret_request = bool((retrieval.get("intent") or {}).get("is_secret_request"))
        sec_status, sec_data = _resolve_secure_refs(item, db, is_secret_request)
        secure_refs = sec_data if sec_status == "resolved" else None

        ctx = _build_context(item, secure_refs, recipient_token)
        if edit_instructions:
            ctx["edit_instructions"] = edit_instructions

        llm = LLMService.generate_draft(draft_type, ctx, results, source_id=source_id)
        if (not llm.succeeded or not llm.data or not _verify_citations(llm.data, results)
                or not _verify_secure_refs(llm.data, secure_refs)):
            if not llm.succeeded or not llm.data:
                reason = f"llm_{llm.status}"
            elif not _verify_citations(llm.data, results):
                reason = "unverifiable_citation"
            else:
                reason = "unverifiable_secure_ref"
            _audit(db, "DRAFT_FAILED", item, source_id, {"reason": reason, "action_id": action.id, "phase": "regenerate"})
            db.commit()
            raise ValueError(f"regeneration failed to produce a verifiable draft ({reason})")

        new_payload = _shape_payload(draft_type, action_type, item, llm.data, results,
                                     confidence, conflict_flag, has_email, has_aad, recipient_token)
        if secure_refs:
            new_payload["secure_refs"] = secure_refs
        # Derived state — ALWAYS recompute the category from the CURRENT transcript
        # context on regenerate (participants/self may have changed). Never reuse a
        # stale cached value; only carry prior fields if the transcript is gone.
        # (transcript was already fetched above for recipient resolution.)
        if transcript is not None:
            new_payload.update(_classify_followup(item, transcript))
        else:
            for _k in ("followup_category", "classification_reason", "classification_version", "counterparty"):
                if _k in prev_payload:
                    new_payload[_k] = prev_payload[_k]
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
