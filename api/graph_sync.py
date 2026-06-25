"""
Graph Sync API — POST /graph/sync

Triggers ingestion for one or all Graph source types.
Each sync run follows the 9-stage pipeline from the Phase 2A blueprint:
  Stage 1  CONNECTOR   — fetch raw payloads from Graph API
  Stage 2  NORMALIZE   — map to GraphSourceItem
  Stage 3  DEDUP       — skip already-ingested external_ids
  Stage 4  THREAT      — score payload; quarantine if score >= 0.80
  Stage 5  MASK+VAULT  — PreprocessingService.mask_pii_indexed() + VaultService.store_tokens()
  Stage 6  CLASSIFY    — sensitivity label on masked text
  Stage 7  STORE       — write SourceItem + source-specific table + ThreatAssessment
  Stage 8  DELTA       — persist updated delta token
  Stage 9  AUDIT       — write INGEST audit event

Stage 7 (LLM knowledge extraction) is Phase 2B — not yet wired.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database.connection import SessionLocal
from database.models import (
    SourceItem, Email, TeamsMessage, CalendarEvent,
    ThreatAssessment, GraphSyncState, AuditEvent,
)
from services.graph.normalizer import GraphNormalizer, GraphSourceItem
from services.graph.connectors.email_connector import EmailConnector
from services.graph.connectors.chat_connector import ChatConnector
from services.graph.connectors.calendar_connector import CalendarConnector
from services.graph.connectors.transcript_connector import TranscriptConnector
from services.preprocessing_service import PreprocessingService
from services.vault_service import VaultService
from services.threat_engine import ThreatEngine, ThreatScore
from services import transcript_ingestion_service
from services import secure_store_service
from utils.config import settings
from utils.logger import get_logger
from utils.time_utils import utcnow

router = APIRouter(prefix="/graph", tags=["graph"])
logger = get_logger("api.graph_sync")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Response schema ────────────────────────────────────────────────────────────

class SyncResult(BaseModel):
    source_type:  str
    fetched:      int
    ingested:     int
    skipped_dup:  int
    quarantined:  int
    errors:       int
    delta_updated: bool


# ── Helper: get/upsert sync state ──────────────────────────────────────────────

def _get_sync_state(db: Session, source_type: str, upn: str) -> GraphSyncState:
    state = db.query(GraphSyncState).filter_by(
        source_type=source_type, user_upn=upn
    ).first()
    if state is None:
        state = GraphSyncState(source_type=source_type, user_upn=upn)
        db.add(state)
        db.flush()
    return state


_TOKEN_RE = __import__("re").compile(r"<([A-Z_]+_\d+)>")


def _scope_tokens(token_map: dict, masked_text: str, source_id: int) -> tuple[dict, str]:
    """
    Re-key per-document indexed tokens to globally-unique, source-scoped names so
    they never collide in the global vault_tokens.token namespace.
    e.g. <PERSON_1> → <S42_PERSON_1>. Keeps masked_text and token_map consistent.
    """
    prefix = f"S{source_id}_"
    new_map = {prefix + k: v for k, v in token_map.items()}
    new_masked = _TOKEN_RE.sub(lambda m: f"<{prefix}{m.group(1)}>", masked_text)
    return new_map, new_masked


# ── Secure-store credential sync (Phase 2) ─────────────────────────────────────
# Ingestion-only: /graph/sync detects → masks → normalizes → upserts confidential
# values into the SINGLE SecureStore. After this, the Graph message is a transient
# source — NO downstream component (draft generation, reveal, send, retrieval) ever
# reads Teams/Email again; credentials resolve exclusively through SecureStoreService.

# Confidential PII entity types (PreprocessingService recognizers) → secure-store
# credential_type taxonomy. Extend as new recognizers are added.
_CREDENTIAL_ENTITY_TYPES = {
    "API_KEY":           "api_key",
    "AWS_KEY":           "api_key",
    "CONNECTION_STRING": "connection_string",
}

def _infer_system_and_context(descriptor_text: str) -> tuple:
    """Read the (non-secret) descriptor words near a credential to infer
    system_name + environment. Delegates to secure_store_service so Phase 2 sync
    and Phase 4 resolution share ONE vocab and can never drift (the descriptor a
    credential is stored under must equal the one it is later resolved by). Never
    reads the secret value itself, never sends anything to an LLM."""
    return secure_store_service.infer_system_and_context(descriptor_text)


def _credential_provenance(item: GraphSourceItem, source_id: int, vault_token: str) -> dict:
    """Rich provenance so a credential version stays traceable back to its
    originating Teams/Email message WITHOUT that message being read again at
    runtime."""
    md = item.metadata or {}
    prov = {
        "message_id":     item.external_id,
        "thread_id":      item.thread_id,
        "sender":         item.sender,
        "sender_name":    item.sender_name,
        "subject":        item.subject,
        "source_type":    item.source_type,
        "source_item_id": source_id,
        "vault_token":    vault_token,
    }
    for k in ("chat_id", "channel_id", "team_id", "conversation_id"):
        if md.get(k):
            prov[k] = md[k]
    return {k: v for k, v in prov.items() if v is not None}


def _sync_credentials_to_store(
    item: GraphSourceItem,
    source_id: int,
    scoped_token_map: dict,
    redactions: list,
    descriptor_text: str,
    db: Session,
) -> int:
    """
    Detect → normalize → upsert every confidential value in this Graph message into
    the single SecureStore. READ-ONLY w.r.t. the masking pipeline: it consumes the
    already-produced redaction set + in-memory token values; it never mutates
    token_map, masked_text, or the transcript/source payloads. Best-effort — a
    store failure is logged and never aborts message ingestion.
    """
    logger.info(
        "[CREDTRACE] Credential Sync -> ENTERED source_id=%s redaction_count=%s redactions=%s",
        source_id, len(redactions), redactions,
    )
    if not redactions:
        logger.warning(
            "[CREDTRACE] Credential Sync -> NO redactions for source_id=%s — STOP "
            "(nothing was detected/masked upstream)", source_id,
        )
        return 0

    system_name, context = _infer_system_and_context(descriptor_text)
    logger.info(
        "[CREDTRACE] Credential Sync -> inferred system_name=%s context=%s",
        system_name, context,
    )
    synced = 0
    seen: set = set()
    for r in redactions:
        credential_type = _CREDENTIAL_ENTITY_TYPES.get(r.get("type"))
        logger.info(
            "[CREDTRACE] Credential Sync -> token=%s entity_type=%s mapped_credential_type=%s",
            r.get("token"),
            r.get("type"),
            credential_type,
        )
        if credential_type is None:
            logger.info(
                "[CREDTRACE] Credential Sync -> SKIP token=%s (entity_type %s is not a "
                "credential type in _CREDENTIAL_ENTITY_TYPES)", r.get("token"), r.get("type"),
            )
            continue
        scoped = f"S{source_id}_{r.get('token')}"
        if scoped in seen:
            continue
        seen.add(scoped)
        value = scoped_token_map.get(scoped)
        # plaintext existence only — never the secret itself.
        logger.info(
            "[CREDTRACE] Credential Sync -> scoped_token=%s plaintext_exists=%s",
            scoped,
            value is not None,
        )
        if not value:
            logger.warning(
                "[CREDTRACE] Credential Sync -> SKIP scoped_token=%s (no plaintext in "
                "token map — masking/scoping mismatch)", scoped,
            )
            continue
        try:
            logger.info(
                "[CREDTRACE] Credential Sync -> calling upsert_credential type=%s system=%s context=%s",
                credential_type,
                system_name,
                context,
            )
            secure_store_service.upsert_credential(
                value,
                {
                    "credential_type": credential_type,
                    "system_name":     system_name,
                    "context":         context,
                    "owner_token":     None,
                    "source_type":     item.source_type,
                    "source_id":       source_id,
                    "source_metadata": _credential_provenance(item, source_id, scoped),
                },
                db,
            )
            synced += 1
        except Exception:
            logger.exception(
                f"[CREDTRACE] Credential Sync -> upsert FAILED for token {scoped} "
                f"(source_id={source_id}) — full traceback above; message ingestion continues"
            )
    logger.info(
        "[CREDTRACE] Credential Sync -> EXIT source_id=%s synced=%s", source_id, synced
    )
    return synced


def _extract_and_sync_credentials(item: GraphSourceItem, source_id: int, db: Session) -> int:
    """
    Re-mask an item's body and sync any detected credentials into the secure store,
    reusing the EXACT same mask → scope → threat-gate → _sync_credentials_to_store
    path as fresh ingestion (no alternate pipeline). Used by the forced-re-extract
    dedup path and the one-time backfill utility so messages ingested before the
    secure-store sync existed can still populate it. Returns credentials upserted.
    """
    cleaned = PreprocessingService.clean_html(item.body_text)
    masked, token_map, redactions = PreprocessingService.mask_pii_indexed(cleaned)
    if not any(_CREDENTIAL_ENTITY_TYPES.get(r.get("type")) for r in redactions):
        return 0
    # Threat-gate parity with fresh ingestion: never enter attacker-planted values.
    threat = ThreatEngine.score(item.source_type, item.raw_payload)
    if threat.category == "QUARANTINE":
        return 0
    scoped_map, _ = _scope_tokens(token_map, masked, source_id)
    return _sync_credentials_to_store(item, source_id, scoped_map, redactions, cleaned, db)


# ── Stage 4–7 core ingest for a single item ───────────────────────────────────

def _ingest_one(
    item: GraphSourceItem,
    db: Session,
    dry_run: bool,
    force_reextract: bool = False,
) -> tuple[str, Optional[Exception]]:
    """
    Run stages 3–7 for one normalized item.
    Returns ("ingested"|"skipped_dup"|"quarantined"|"error", exc_or_None).
    """
    try:
        # ── [CREDTRACE] STAGE: Graph ───────────────────────────────────────────
        # Body length only — never log the raw body, it carries the plaintext secret.
        logger.info(
            "[CREDTRACE] Graph -> external_id=%s source_type=%s body_len=%s",
            item.external_id, item.source_type, len(item.body_text or ""),
        )

        # Stage 3 — dedup check (dialect-safe: generic JSON columns do not
        # support the `.astext` accessor, so filter the external_id in Python).
        existing_id = None
        for (sid, md) in db.query(SourceItem.id, SourceItem.metadata_).filter(
            SourceItem.source_type == item.source_type
        ).all():
            if (md or {}).get("external_id") == item.external_id:
                existing_id = sid
                break
        if existing_id is not None:
            # [CREDTRACE] Likely silent stop: an already-ingested message is NOT
            # re-stored, and its credentials are only synced on force_reextract=True.
            logger.warning(
                "[CREDTRACE] Graph -> DUPLICATE external_id=%s (source_id=%s). "
                "Credential sync runs only when force_reextract=True (currently %s).",
                item.external_id, existing_id, force_reextract,
            )
            # The message is already archived, so we do NOT re-store it. But on a
            # forced re-extract we still scan this freshly-fetched body for
            # credentials and sync them into the secure store — this is the backfill
            # path for messages ingested before the secure-store sync existed.
            if force_reextract and not dry_run:
                try:
                    n = _extract_and_sync_credentials(item, existing_id, db)
                    if n:
                        db.commit()
                        logger.info(
                            f"dup re-extract: synced {n} credential(s) from "
                            f"{item.external_id} (source_id={existing_id})"
                        )
                except Exception:
                    db.rollback()
                    logger.exception(
                        f"dup re-extract credential sync failed for {item.external_id}"
                    )
            return "skipped_dup", None

        # Stage 4 — threat scoring
        threat: ThreatScore = ThreatEngine.score(item.source_type, item.raw_payload)

        # Stage 4b — HTML cleanup (Graph bodies are contentType=html)
        cleaned_body = PreprocessingService.clean_html(item.body_text)

        # Stage 5 — mask PII with indexed vault tokens
        masked_text, token_map, redactions = PreprocessingService.mask_pii_indexed(
            cleaned_body
        )

        # Stage 6 — sensitivity classification
        sensitivity = PreprocessingService.classify_sensitivity(
            cleaned_body, redactions
        )
        # Upgrade to RESTRICTED when threat is FLAGGED or worse
        if threat.category in ("FLAGGED", "QUARANTINE") and sensitivity == "CONFIDENTIAL":
            sensitivity = "RESTRICTED"
        # Active prompt-injection content must never be classified low, even when
        # the aggregate threat score stays below the quarantine threshold.
        if threat.injection_score and threat.injection_score > 0 and sensitivity in ("PUBLIC", "INTERNAL"):
            sensitivity = "CONFIDENTIAL"

        if dry_run:
            return "ingested", None

        # Stage 7a — write SourceItem (raw_text = JSON of raw payload, never sent to LLM)
        raw_json = json.dumps(item.raw_payload, default=str)
        source_item = SourceItem(
            source_type       = item.source_type,
            raw_text          = raw_json,
            masked_text       = masked_text,
            sensitivity_label = sensitivity,
            noise_removed     = cleaned_body,
            metadata_         = {
                "external_id": item.external_id,
                "subject":     item.subject,
                "sender":      item.sender,
                "thread_id":   item.thread_id,
                **item.metadata,
            },
        )
        db.add(source_item)
        db.flush()  # get source_item.id
        logger.info(
            "[CREDTRACE] Graph -> assigned source_id=%s for external_id=%s",
            source_item.id, item.external_id,
        )

        # Indexed tokens (PERSON_1, API_KEY_1, …) are per-DOCUMENT, but
        # vault_tokens.token is a GLOBAL unique key. Without scoping, the second
        # message that produces PERSON_1 collides with the first and its entity
        # is silently dropped — its masked placeholder would then resolve to a
        # DIFFERENT message's secret. Re-key every token to a source-scoped,
        # globally-unique name and rewrite the masked text to match.
        token_map, masked_text = _scope_tokens(token_map, masked_text, source_item.id)
        source_item.masked_text = masked_text

        # Stage 7b — write vault tokens
        if token_map:
            VaultService.store_tokens(token_map, source_item.id, db)

        # Stage 7b.2 — sync confidential values into the single SecureStore
        # (Phase 2). Ingestion-only: the Graph message is transient hereafter and
        # is never read again for credential resolution. Reads the already-masked
        # redactions + in-memory token values; does not alter masking or payloads.
        # Skip QUARANTINE messages — never let an attacker-planted value enter the
        # trusted store (the message itself is still archived + threat-assessed).
        logger.info(
            "[CREDTRACE] Threat gate -> category=%s credential_risk=%s injection=%s "
            "(QUARANTINE would skip credential sync)",
            threat.category, threat.credential_risk, threat.injection_score,
        )
        if threat.category != "QUARANTINE":
            _sync_credentials_to_store(item, source_item.id, token_map, redactions, cleaned_body, db)
        else:
            logger.warning(
                "[CREDTRACE] Credential Sync SKIPPED -> message QUARANTINED "
                "(source_id=%s external_id=%s). Execution stops before SecureStore.",
                source_item.id, item.external_id,
            )

        # Stage 7c — write source-specific table
        _write_source_table(item, source_item.id, masked_text, token_map, threat, db)

        # Stage 7d — write ThreatAssessment
        db.add(ThreatAssessment(
            source_id          = source_item.id,
            threat_score       = threat.aggregate,
            category           = threat.category,
            phishing_score     = threat.phishing_score,
            bec_score          = threat.bec_score,
            credential_risk    = threat.credential_risk,
            urgency_score      = threat.urgency_score,
            injection_score    = threat.injection_score,
            social_eng_score   = threat.social_eng_score,
            link_risk_score    = threat.link_risk_score,
            flags              = threat.flags,
            recommended_action = threat.recommended_action,
            notes              = threat.notes,
        ))

        db.commit()
        logger.info(
            "[CREDTRACE] DB Commit -> _ingest_one committed source_id=%s external_id=%s",
            source_item.id, item.external_id,
        )

        outcome = "quarantined" if threat.category == "QUARANTINE" else "ingested"
        return outcome, None

    except Exception as e:
        db.rollback()
        # [CREDTRACE] Full traceback so a swallowed error in any stage above is visible.
        logger.exception(
            "[CREDTRACE] _ingest_one FAILED for %s — execution stopped here; rolled back",
            item.external_id,
        )
        return "error", e


def _run_transcript_source(upn: str, db: Session, dry_run: bool, force_reextract: bool) -> SyncResult:
    """
    Transcript ingestion takes a dedicated path: TranscriptIngestionService owns
    archive/threat-scan/mask/persist/extract/knowledge-persist internally (including
    its own LLM extraction step), so this bypasses GraphNormalizer.normalize() and
    _write_source_table() — that generic dispatch was built for the lighter email/
    chat/calendar pipeline, not for a step that also calls the LLM.
    """
    result = SyncResult(
        source_type="transcript", fetched=0, ingested=0,
        skipped_dup=0, quarantined=0, errors=0, delta_updated=False,
    )

    try:
        state = _get_sync_state(db, "transcript", upn)
        if not dry_run:
            db.commit()

        connector = TranscriptConnector(upn)
        transcripts, new_cursor = connector.fetch_since(state.delta_token)
        result.fetched = len(transcripts)

        if dry_run:
            return result

        for transcript in transcripts:
            try:
                outcome = transcript_ingestion_service.ingest(transcript, db, force_reextract=force_reextract)
            except Exception as e:
                logger.error(f"Transcript ingestion failed for {transcript.metadata.external_id}: {e}")
                result.errors += 1
                continue

            if outcome["outcome"] == "ingested":
                result.ingested += 1
            elif outcome["outcome"] == "quarantined":
                result.quarantined += 1
                result.ingested += 1
            elif outcome["outcome"] in ("skipped_dup", "skipped_idempotent"):
                result.skipped_dup += 1

        if new_cursor and new_cursor != state.delta_token:
            state.delta_token = new_cursor
            state.last_synced_at = utcnow()
            state.items_synced = (state.items_synced or 0) + result.ingested
            db.commit()
            result.delta_updated = True

    except Exception as e:
        logger.error(f"_run_transcript_source failed: {e}")
        result.errors += 1
        try:
            db.rollback()
        except Exception:
            pass

    return result


def _write_source_table(
    item: GraphSourceItem,
    source_id: int,
    masked_text: str,
    token_map: dict,
    threat: ThreatScore,
    db: Session,
) -> None:
    """Write the source-specific normalized record (email / teams_message / calendar_event)."""

    # Build a reverse map: original_value → token_name (for participant/sender lookup)
    rev = {v: k for k, v in token_map.items()}

    if item.source_type == "outlook_email":
        db.add(Email(
            source_id       = source_id,
            external_id     = item.external_id,
            internet_message_id = item.metadata.get("internet_message_id"),
            subject_masked  = _mask_value(item.subject, rev, masked_text),
            from_token      = rev.get(item.sender or ""),
            from_name_token = rev.get(item.sender_name or ""),
            to_tokens       = [rev.get(r, "") for r in item.recipients],
            body_size_chars = len(item.body_text),
            has_attachments = item.has_attachments,
            importance      = item.importance,
            received_at     = item.timestamp or utcnow(),
            thread_id       = item.thread_id,
            sensitivity_label = _sensitivity_from_threat(threat),
            threat_score    = threat.aggregate,
            is_quarantined  = threat.category == "QUARANTINE",
        ))

    elif item.source_type in ("teams_chat", "teams_channel"):
        db.add(TeamsMessage(
            source_id          = source_id,
            external_id        = item.external_id,
            chat_id            = item.metadata.get("chat_id"),
            channel_id         = item.metadata.get("channel_id"),
            team_id            = item.metadata.get("team_id"),
            message_type       = item.metadata.get("message_type", "message"),
            from_token         = rev.get(item.sender or ""),
            from_name_token    = rev.get(item.sender_name or ""),
            body_masked        = masked_text,
            mentions_tokens    = [rev.get(p, "") for p in item.participants],
            importance         = item.importance,
            is_channel_message = item.source_type == "teams_channel",
            thread_id          = item.thread_id,
            reply_to_id        = item.metadata.get("reply_to_id"),
            has_attachments    = item.has_attachments,
            sensitivity_label  = _sensitivity_from_threat(threat),
            threat_score       = threat.aggregate,
            is_quarantined     = threat.category == "QUARANTINE",
            sent_at            = item.timestamp or utcnow(),
        ))

    elif item.source_type == "calendar":
        db.add(CalendarEvent(
            source_id         = source_id,
            external_id       = item.external_id,
            subject_masked    = _mask_value(item.subject, rev, masked_text),
            organizer_token   = rev.get(item.sender or ""),
            attendee_tokens   = [rev.get(r, "") for r in item.recipients],
            start_at          = item.timestamp or utcnow(),
            end_at            = item.end_time or item.timestamp or utcnow(),
            is_online_meeting = bool(item.metadata.get("is_online_meeting")),
            meeting_url       = item.metadata.get("meeting_url"),
            body_masked       = masked_text,
            importance        = item.importance,
            sensitivity_label = _sensitivity_from_threat(threat),
            is_cancelled      = bool(item.metadata.get("is_cancelled")),
        ))


def _mask_value(value: Optional[str], rev: dict, masked_text: str) -> Optional[str]:
    """Return the vault token for a specific original value, or the raw value if not in vault."""
    if value is None:
        return None
    return rev.get(value, value)


def _sensitivity_from_threat(threat: ThreatScore) -> str:
    if threat.category == "QUARANTINE":
        return "RESTRICTED"
    if threat.category == "FLAGGED":
        return "CONFIDENTIAL"
    return "INTERNAL"


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("/sync", response_model=SyncResult)
def trigger_sync(
    source: str = Query(
        default="email",
        description="Source type: email | chat | calendar | transcript | all",
    ),
    upn: Optional[str] = Query(
        default=None,
        description="UPN to sync (defaults to GRAPH_SERVICE_UPN from config)",
    ),
    dry_run: bool = Query(
        default=False,
        description="Validate pipeline without writing to DB",
    ),
    force_reextract: bool = Query(
        default=False,
        description="Re-fetch ALL history: rewinds the chat/email/calendar cursor and "
                    "credential-syncs already-ingested messages; for transcripts, also "
                    "re-runs LLM extraction. Off = incremental (cursor-based) sync.",
    ),
    db: Session = Depends(get_db),
):
    """
    Trigger an incremental Graph sync for the given source type.
    Returns a summary of items fetched, ingested, quarantined, and errors.
    """
    target_upn = upn or settings.GRAPH_SERVICE_UPN
    if not target_upn:
        raise HTTPException(400, "UPN not specified and GRAPH_SERVICE_UPN not configured.")

    if not VaultService.is_configured() and not dry_run:
        raise HTTPException(
            503,
            "VAULT_MASTER_KEY not configured — cannot store tokens safely. "
            "Add VAULT_MASTER_KEY=<64-hex-chars> to .env and restart."
        )

    sources_to_run = ["email", "chat", "calendar", "transcript"] if source == "all" else [source]
    total = SyncResult(
        source_type=source, fetched=0, ingested=0,
        skipped_dup=0, quarantined=0, errors=0, delta_updated=False,
    )

    for src in sources_to_run:
        result = _run_source(src, target_upn, db, dry_run, force_reextract=force_reextract)
        total.fetched     += result.fetched
        total.ingested    += result.ingested
        total.skipped_dup += result.skipped_dup
        total.quarantined += result.quarantined
        total.errors      += result.errors
        total.delta_updated = total.delta_updated or result.delta_updated

    logger.info(
        f"GraphSync complete — sources={sources_to_run} upn={target_upn} "
        f"fetched={total.fetched} ingested={total.ingested} "
        f"quarantined={total.quarantined} errors={total.errors}"
    )
    return total


def _run_source(source: str, upn: str, db: Session, dry_run: bool, force_reextract: bool = False) -> SyncResult:
    result = SyncResult(
        source_type=source, fetched=0, ingested=0,
        skipped_dup=0, quarantined=0, errors=0, delta_updated=False,
    )

    if source == "transcript":
        return _run_transcript_source(upn, db, dry_run, force_reextract)

    try:
        state = _get_sync_state(db, source, upn)
        if not dry_run:
            db.commit()

        # Stage 1 — fetch. A forced re-extract rewinds the cursor to None so the
        # connector re-fetches ALL history (the same code path the very first sync
        # uses) — this is what lets credential backfill reach messages ingested
        # before the secure-store sync existed. Incremental syncs are unchanged.
        raw_items: list[dict] = []
        new_delta_token = state.delta_token
        cursor = None if force_reextract else state.delta_token

        if source == "email":
            connector = EmailConnector(upn)
            raw_items, new_delta_token = connector.fetch_delta(cursor)

        elif source == "chat":
            connector = ChatConnector(upn)
            raw_items, new_delta_token = connector.fetch_since(cursor)

        elif source == "calendar":
            connector = CalendarConnector(upn)
            raw_items, new_delta_token = connector.fetch_delta(cursor)

        else:
            logger.warning(f"_run_source: unknown source={source!r}")
            return result

        result.fetched = len(raw_items)

        # Stages 2–7 — per item
        source_type_map = {
            "email": "outlook_email",
            "chat":  "teams_chat",
            "calendar": "calendar",
        }
        graph_source_type = source_type_map.get(source, source)

        for raw in raw_items:
            try:
                normalized = GraphNormalizer.normalize(graph_source_type, raw)
            except Exception as e:
                logger.warning(f"Normalization failed for item: {e}")
                result.errors += 1
                continue

            outcome, exc = _ingest_one(normalized, db, dry_run, force_reextract=force_reextract)
            if outcome == "ingested":
                result.ingested += 1
            elif outcome == "quarantined":
                result.quarantined += 1
                result.ingested += 1   # quarantined items are stored, just flagged
            elif outcome == "skipped_dup":
                result.skipped_dup += 1
            elif outcome == "error":
                result.errors += 1

        # Stage 8 — persist delta token
        if not dry_run and new_delta_token and new_delta_token != state.delta_token:
            state.delta_token    = new_delta_token
            state.last_synced_at = utcnow()
            state.items_synced   = (state.items_synced or 0) + result.ingested
            db.commit()
            result.delta_updated = True

    except Exception as e:
        # [CREDTRACE] Full traceback so a swallowed connector/normalize error is visible.
        logger.exception(f"[CREDTRACE] _run_source FAILED source={source}")
        result.errors += 1
        try:
            db.rollback()
        except Exception:
            pass

    return result


@router.post("/sync/transcript/mock-trigger", response_model=SyncResult)
def trigger_mock_transcript_sync(
    since_iso: Optional[str] = Query(
        default=None,
        description="Only ingest mock transcripts created after this ISO timestamp",
    ),
    force_reextract: bool = Query(
        default=False,
        description="Bypass the idempotency check and re-run LLM extraction",
    ),
    db: Session = Depends(get_db),
):
    """
    Development-only: manually trigger transcript ingestion against the
    MockTranscriptProvider fixtures, without waiting on meeting_scheduler's
    post-meeting poll. Disabled outside development to avoid exposing a
    synthetic-data ingestion path in production.
    """
    if settings.APP_ENV == "production":
        raise HTTPException(404, "Not available outside development.")
    if settings.TRANSCRIPT_PROVIDER != "mock":
        raise HTTPException(
            400,
            f"TRANSCRIPT_PROVIDER is {settings.TRANSCRIPT_PROVIDER!r} — this endpoint only drives the mock provider.",
        )

    connector = TranscriptConnector(settings.GRAPH_SERVICE_UPN)
    transcripts, _cursor = connector.fetch_since(since_iso)

    result = SyncResult(
        source_type="transcript", fetched=len(transcripts), ingested=0,
        skipped_dup=0, quarantined=0, errors=0, delta_updated=False,
    )
    for transcript in transcripts:
        try:
            outcome = transcript_ingestion_service.ingest(transcript, db, force_reextract=force_reextract)
        except Exception as e:
            logger.error(f"mock-trigger: ingestion failed for {transcript.metadata.external_id}: {e}")
            result.errors += 1
            continue
        if outcome["outcome"] == "ingested":
            result.ingested += 1
        elif outcome["outcome"] == "quarantined":
            result.quarantined += 1
            result.ingested += 1
        elif outcome["outcome"] in ("skipped_dup", "skipped_idempotent"):
            result.skipped_dup += 1

    return result


# ── One-time credential backfill / repair utility ─────────────────────────────
# NOT a permanent pipeline component: a migration tool that replays already-stored
# SourceItem rows through the EXISTING _sync_credentials_to_store() path, for
# messages ingested before the secure-store sync existed. Idempotent (upsert_credential
# de-dups), audited, masking/versioning/rotation all preserved.

def _backfill_one_source_item(si: SourceItem, db: Session) -> int:
    """Reconstruct a GraphSourceItem carrier from a stored SourceItem and run the
    existing credential-sync path. Uses noise_removed (the cleaned body retained at
    ingestion) so credential values can be re-detected and encrypted into the store."""
    body = si.noise_removed or ""
    if not body:
        return 0
    md = si.metadata_ or {}
    carrier = GraphSourceItem(
        source_type=si.source_type,
        external_id=md.get("external_id") or f"backfill-{si.id}",
        raw_payload={},
        body_text=body,
        subject=md.get("subject"),
        sender=md.get("sender"),
        thread_id=md.get("thread_id"),
        metadata=md,
    )
    return _extract_and_sync_credentials(carrier, si.id, db)


@router.post("/credentials/backfill")
def backfill_credentials(
    source: str = Query("chat,email", description="comma list of: chat, email, channel"),
    limit: int = Query(2000, description="max SourceItem rows to scan"),
    db: Session = Depends(get_db),
):
    """
    One-time repair: populate the Secure Store from historical SourceItem rows that
    were ingested before continuous credential sync existed. Reuses the existing
    detection → masking → _sync_credentials_to_store() path (no new pipeline), is
    idempotent and audited, and skips QUARANTINE items (parity with live ingestion).
    """
    type_map = {"chat": "teams_chat", "email": "outlook_email", "channel": "teams_channel"}
    wanted = [type_map[s.strip()] for s in source.split(",") if s.strip() in type_map]
    if not wanted:
        raise HTTPException(400, f"source must be a comma list of {sorted(type_map)}")

    quarantined = {
        sid for (sid,) in db.query(ThreatAssessment.source_id)
        .filter(ThreatAssessment.category == "QUARANTINE").all()
    }
    rows = (
        db.query(SourceItem)
        .filter(SourceItem.source_type.in_(wanted))
        .order_by(SourceItem.id)
        .limit(limit)
        .all()
    )

    scanned = synced = skipped_quarantine = 0
    for si in rows:
        if si.id in quarantined:
            skipped_quarantine += 1
            continue
        scanned += 1
        try:
            n = _backfill_one_source_item(si, db)
            if n:
                db.commit()
                synced += n
            else:
                db.rollback()
        except Exception:
            db.rollback()
            logger.exception(f"credential backfill failed for source_item {si.id}")

    logger.info(
        f"credential backfill: scanned={scanned} synced={synced} "
        f"skipped_quarantine={skipped_quarantine} types={wanted}"
    )
    return {
        "scanned": scanned,
        "credentials_synced": synced,
        "skipped_quarantine": skipped_quarantine,
        "source_types": wanted,
    }
