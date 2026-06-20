"""
TranscriptIngestionService — orchestrator for everything past TranscriptConnector.

This is the only place that writes MeetingTranscript / TranscriptSegment /
KnowledgeItem rows for a transcript. Each responsibility below (archive,
threat scan, masking, transcript persistence, idempotency check, intelligence
extraction, knowledge persistence) is delegated to existing, separately
testable code — this service only sequences them and decides when to
short-circuit.
"""

import hashlib
import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from database.models import AuditEvent, KnowledgeItem, MeetingTranscript, SourceItem, TranscriptSegment
from services import knowledge_evolution_service, knowledge_persistence_service
from services.graph.transcript_models import Transcript
from services.llm_audit_service import LLMAuditService
from services.llm_service import LLMService
from services.meeting_intelligence_model import MeetingIntelligence
from services.preprocessing_service import PreprocessingService
from services.threat_engine import ThreatEngine
from services.vault_service import VaultService
from utils.logger import get_logger

logger = get_logger("services.transcript_ingestion")

_LLM_CALL_TYPE = "meeting_intelligence_extract"
_TOKEN_RE = re.compile(r"<([A-Za-z_]+_\d+)>")


# ── Token scoping (mirrors api/graph_sync.py._scope_tokens) ─────────────────
# vault_tokens.token is globally unique, but mask_pii_indexed()'s per-document
# numbering restarts for every transcript — without scoping, meeting 2's
# <PERSON_1> would collide with meeting 1's in the vault and resolve to the
# wrong identity. Every token gets a source_id-scoped name before storage.

def _scope_text(text: str, source_id: int) -> str:
    return _TOKEN_RE.sub(lambda m: f"<S{source_id}_{m.group(1)}>", text)


def _scope_token_map(token_map: dict[str, str], source_id: int) -> dict[str, str]:
    return {f"S{source_id}_{name}": value for name, value in token_map.items()}


def _scope_name(name: str, source_id: int) -> str:
    return f"S{source_id}_{name}"


# ── Masking with stable cross-utterance tokens ───────────────────────────────
# mask_pii_indexed() is only stable WITHIN a single call. Calling it once per
# utterance would assign different token numbers to the same speaker mentioned
# in two different utterances. Instead, mask every text independently but merge
# results into one (entity_type, value) -> token registry, so repeated values
# anywhere in the meeting collapse onto the same token.

def _mask_texts_stably(texts: list[str]) -> tuple[list[str], dict[str, str]]:
    value_to_token: dict[tuple, str] = {}
    type_counters: dict[str, int] = {}
    global_token_map: dict[str, str] = {}
    masked_texts: list[str] = []

    for text in texts:
        local_masked, local_token_map, _redactions = PreprocessingService.mask_pii_indexed(text)
        remap: dict[str, str] = {}
        for local_name, value in local_token_map.items():
            entity_type = local_name.rsplit("_", 1)[0]
            key = (entity_type, value)
            if key in value_to_token:
                global_name = value_to_token[key]
            else:
                type_counters[entity_type] = type_counters.get(entity_type, 0) + 1
                global_name = f"{entity_type}_{type_counters[entity_type]}"
                value_to_token[key] = global_name
                global_token_map[global_name] = value
            remap[local_name] = global_name
        rewritten = _TOKEN_RE.sub(lambda m: f"<{remap.get(m.group(1), m.group(1))}>", local_masked)
        masked_texts.append(rewritten)

    return masked_texts, global_token_map


# ── Speaker identity tokenization ────────────────────────────────────────────

def _assign_speaker_tokens(transcript: Transcript) -> dict[str, str]:
    """
    Returns {participant.identity_key(): "SPEAKER_n"} for every participant
    AND every speaker referenced in an utterance but absent from the official
    roster (e.g. an external guest) — so even unlisted speakers get a stable,
    vault-backed token rather than appearing as plain text.
    """
    ordered: list = list(transcript.participants)
    seen = {p.identity_key() for p in ordered}
    for utterance in transcript.utterances:
        if utterance.speaker and utterance.speaker.identity_key() not in seen:
            ordered.append(utterance.speaker)
            seen.add(utterance.speaker.identity_key())

    return {p.identity_key(): f"SPEAKER_{i + 1}" for i, p in enumerate(ordered) if p.identity_key()}


def _identity_token_map(transcript: Transcript, speaker_tokens: dict[str, str]) -> dict[str, str]:
    """Builds the vault token_map for participant identity (display_name/email/aad_object_id)."""
    identity_map: dict[str, str] = {}
    seen_keys: set = set()
    for participant in list(transcript.participants) + [u.speaker for u in transcript.utterances if u.speaker]:
        key = participant.identity_key()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        token = speaker_tokens.get(key)
        if not token:
            continue
        if participant.display_name:
            identity_map[token] = participant.display_name
        if participant.email:
            identity_map[f"{token}_EMAIL"] = participant.email
        if participant.aad_object_id:
            identity_map[f"{token}_AAD"] = participant.aad_object_id
    return identity_map


# ── Dedup / idempotency helpers ──────────────────────────────────────────────

def _find_existing_source_item(db: Session, external_id: str) -> Optional[SourceItem]:
    """Same dedup-by-external_id pattern used for email/chat/calendar in api/graph_sync.py."""
    for source_item in db.query(SourceItem).filter(SourceItem.source_type == "transcript").all():
        if (source_item.metadata_ or {}).get("external_id") == external_id:
            return source_item
    return None


def _content_hash(raw_source: dict) -> str:
    canonical = json.dumps(raw_source, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_prior_successful_extraction(db: Session, content_hash: str, exclude_transcript_id: int):
    """
    Looks for an earlier MeetingTranscript with identical content that already
    completed extraction successfully — the basis of the idempotency check.
    """
    prior_transcript = (
        db.query(MeetingTranscript)
        .filter(
            MeetingTranscript.content_hash == content_hash,
            MeetingTranscript.id != exclude_transcript_id,
        )
        .order_by(MeetingTranscript.id.desc())
        .first()
    )
    if not prior_transcript:
        return None, None
    prior_call = LLMAuditService.find_successful_call(db, prior_transcript.source_id, _LLM_CALL_TYPE)
    return prior_transcript, prior_call


# ── Main orchestration ───────────────────────────────────────────────────────

def ingest(transcript: Transcript, db: Session, force_reextract: bool = False) -> dict:
    """
    Run the full pipeline for one Transcript: archive -> threat scan -> mask ->
    persist -> idempotency check -> extract -> persist intelligence.

    Returns a summary dict: {"outcome", "source_id", "meeting_transcript_id",
    "knowledge_item_count"}.
    """
    metadata = transcript.metadata

    # ── 1. Dedup — same transcript resource already ingested ───────────────
    existing = _find_existing_source_item(db, metadata.external_id)
    if existing is not None:
        return {"outcome": "skipped_dup", "source_id": existing.id, "meeting_transcript_id": None, "knowledge_item_count": 0}

    try:
        # ── Archive ──────────────────────────────────────────────────────
        source_item = SourceItem(
            source_type="transcript",
            raw_text=json.dumps(metadata.raw_source, default=str),
            metadata_={
                "external_id": metadata.external_id,
                "meeting_id": metadata.meeting_id,
                "subject": metadata.subject,
            },
        )
        db.add(source_item)
        db.flush()  # need source_item.id for token scoping
        source_id = source_item.id

        # ── Threat scan (raw, pre-mask content) ─────────────────────────
        full_raw_text = "\n".join(u.text for u in transcript.utterances)
        participant_labels = [
            p.display_name or p.email or p.aad_object_id or "" for p in transcript.participants
        ]
        threat = ThreatEngine.score_transcript({"content": full_raw_text, "participants": participant_labels})
        is_quarantined = threat.category == "QUARANTINE"
        sensitivity = "RESTRICTED" if is_quarantined else ("CONFIDENTIAL" if threat.category == "FLAGGED" else "INTERNAL")
        source_item.sensitivity_label = sensitivity

        # ── Masking (stable tokens across the whole meeting) ────────────
        speaker_tokens = _assign_speaker_tokens(transcript)
        texts_to_mask = [metadata.subject or ""] + [u.text for u in transcript.utterances]
        masked_texts, content_token_map = _mask_texts_stably(texts_to_mask)
        subject_masked, *segment_masked_texts = masked_texts

        identity_token_map = _identity_token_map(transcript, speaker_tokens)

        # Scope every token + masked text to this source_id before they ever
        # reach the vault or get persisted, so they can never collide with
        # another meeting's tokens.
        subject_masked = _scope_text(subject_masked, source_id)
        segment_masked_texts = [_scope_text(t, source_id) for t in segment_masked_texts]
        combined_token_map = {**content_token_map, **identity_token_map}
        scoped_token_map = _scope_token_map(combined_token_map, source_id)
        scoped_speaker_tokens = {key: _scope_name(tok, source_id) for key, tok in speaker_tokens.items()}

        source_item.noise_removed = full_raw_text
        db.flush()

        content_hash = _content_hash(metadata.raw_source)

        # ── Persist transcript + segments ───────────────────────────────
        organizer_token = (
            scoped_speaker_tokens.get(metadata.organizer.identity_key())
            if metadata.organizer and metadata.organizer.identity_key()
            else None
        )
        meeting_transcript = MeetingTranscript(
            source_id=source_id,
            external_id=metadata.external_id,
            meeting_id=metadata.meeting_id,
            subject_masked=subject_masked,
            participant_tokens=sorted(set(scoped_speaker_tokens.values())),
            duration_seconds=metadata.duration_seconds,
            segment_count=len(transcript.utterances),
            word_count=sum(len(t.split()) for t in segment_masked_texts),
            masked_content="\n".join(segment_masked_texts),
            sensitivity_label=sensitivity,
            threat_score=threat.aggregate,
            meeting_start=metadata.start_time,
            meeting_end=metadata.end_time,
            organizer_token=organizer_token,
            content_hash=content_hash,
        )
        db.add(meeting_transcript)
        db.flush()

        for utterance, masked_text in zip(transcript.utterances, segment_masked_texts):
            speaker_token = (
                scoped_speaker_tokens.get(utterance.speaker.identity_key())
                if utterance.speaker and utterance.speaker.identity_key()
                else None
            )
            db.add(TranscriptSegment(
                transcript_id=meeting_transcript.id,
                speaker_token=speaker_token,
                start_ms=utterance.start_ms,
                end_ms=utterance.end_ms,
                text_masked=masked_text,
                sequence_index=utterance.sequence_index,
            ))

        if scoped_token_map:
            VaultService.store_tokens(scoped_token_map, source_id, db, session_context=metadata.meeting_id)

        db.add(AuditEvent(
            event_type="INGEST",
            actor="system",
            source_id=source_id,
            outcome="SUCCESS",
            event_data={"source_type": "transcript", "external_id": metadata.external_id, "threat_category": threat.category},
        ))
        db.commit()

    except Exception:
        db.rollback()
        logger.exception(f"TranscriptIngestionService: failed to archive/persist transcript {metadata.external_id}")
        raise

    if is_quarantined:
        logger.warning(
            f"Transcript {metadata.external_id} QUARANTINED (score={threat.aggregate}) — "
            f"skipping intelligence extraction"
        )
        return {
            "outcome": "quarantined",
            "source_id": source_id,
            "meeting_transcript_id": meeting_transcript.id,
            "knowledge_item_count": 0,
        }

    # ── Idempotency check ───────────────────────────────────────────────────
    if not force_reextract:
        prior_transcript, prior_call = _find_prior_successful_extraction(db, content_hash, meeting_transcript.id)
        if prior_transcript and prior_call:
            existing_items = db.query(KnowledgeItem).filter(KnowledgeItem.llm_call_log_id == prior_call.id).count()
            logger.info(
                f"Transcript {metadata.external_id}: identical content already extracted "
                f"(prior source_id={prior_transcript.source_id}) — skipping re-extraction"
            )
            return {
                "outcome": "skipped_idempotent",
                "source_id": source_id,
                "meeting_transcript_id": meeting_transcript.id,
                "knowledge_item_count": existing_items,
            }

    # ── Intelligence extraction ─────────────────────────────────────────────
    llm_context_lines = [
        f"{scoped_speaker_tokens.get(u.speaker.identity_key(), 'UNKNOWN') if u.speaker else 'UNKNOWN'}: {text}"
        for u, text in zip(transcript.utterances, segment_masked_texts)
    ]
    result = LLMService.extract_meeting_intelligence(
        masked_transcript="\n".join(llm_context_lines),
        participant_tokens=sorted(set(scoped_speaker_tokens.values())),
        source_id=source_id,
    )

    if not result.succeeded:
        logger.warning(
            f"Transcript {metadata.external_id}: intelligence extraction failed "
            f"(status={result.status}) — transcript stored, no KnowledgeItems created"
        )
        return {
            "outcome": "ingested",
            "source_id": source_id,
            "meeting_transcript_id": meeting_transcript.id,
            "knowledge_item_count": 0,
        }

    intelligence = MeetingIntelligence.from_llm_data(result.data)

    try:
        created = knowledge_persistence_service.persist(
            intelligence=intelligence,
            source_id=source_id,
            llm_call_log_id=result.call_log_id,
            meeting_start=metadata.start_time,
            db=db,
        )
        # Plan 2: resolve versioning/supersession + populate embeddings for the
        # newly-persisted rows inside this same transaction (so the row locks and
        # the commit are atomic). Duplicate confirmations are collapsed here, so
        # the surviving active-row count can be < len(created).
        knowledge_evolution_service.resolve_created_items(created, db)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(f"TranscriptIngestionService: knowledge persistence failed for {metadata.external_id}")
        raise

    # Count rows that actually survived this ingest — duplicate confirmations are
    # collapsed onto pre-existing rows (different source_id) and removed here.
    surviving = db.query(KnowledgeItem).filter(KnowledgeItem.source_id == source_id).count()
    return {
        "outcome": "ingested",
        "source_id": source_id,
        "meeting_transcript_id": meeting_transcript.id,
        "knowledge_item_count": surviving,
    }


def ingest_many(transcripts: list[Transcript], db: Session, force_reextract: bool = False) -> list[dict]:
    return [ingest(t, db, force_reextract=force_reextract) for t in transcripts]
