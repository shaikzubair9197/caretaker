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


# ── Stage 4–7 core ingest for a single item ───────────────────────────────────

def _ingest_one(
    item: GraphSourceItem,
    db: Session,
    dry_run: bool,
) -> tuple[str, Optional[Exception]]:
    """
    Run stages 3–7 for one normalized item.
    Returns ("ingested"|"skipped_dup"|"quarantined"|"error", exc_or_None).
    """
    try:
        # Stage 3 — dedup check (dialect-safe: generic JSON columns do not
        # support the `.astext` accessor, so filter the external_id in Python).
        for (md,) in db.query(SourceItem.metadata_).filter(
            SourceItem.source_type == item.source_type
        ).all():
            if (md or {}).get("external_id") == item.external_id:
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

        outcome = "quarantined" if threat.category == "QUARANTINE" else "ingested"
        return outcome, None

    except Exception as e:
        db.rollback()
        logger.error(f"_ingest_one failed for {item.external_id}: {e}")
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
        description="transcript source only — bypass the idempotency check and re-run LLM extraction",
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

        # Stage 1 — fetch
        raw_items: list[dict] = []
        new_delta_token = state.delta_token

        if source == "email":
            connector = EmailConnector(upn)
            raw_items, new_delta_token = connector.fetch_delta(state.delta_token)

        elif source == "chat":
            connector = ChatConnector(upn)
            raw_items, new_delta_token = connector.fetch_since(state.delta_token)

        elif source == "calendar":
            connector = CalendarConnector(upn)
            raw_items, new_delta_token = connector.fetch_delta(state.delta_token)

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

            outcome, exc = _ingest_one(normalized, db, dry_run)
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
        logger.error(f"_run_source failed source={source}: {e}")
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
