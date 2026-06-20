"""
Transcript ingestion pipeline tests (Plan 1).

Uses an in-memory SQLite DB — no PostgreSQL/Graph required. The Ollama HTTP
call is patched (same convention as tests/test_llm_service.py) so no real
LLM is needed either. Audit writers that open their own independent DB
session (LLMAuditService.log, vault_service._write_audit) are patched out,
exactly like the existing LLM service tests — this test focuses on the
transcript pipeline's own logic (masking, idempotency, quarantine, knowledge
persistence), not on the already-covered audit-writer internals.

Run with:
    cd caretaker
    python -m pytest tests/test_transcript_ingestion.py -v
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import AuditEvent, Base, KnowledgeItem, LLMCallLog, MeetingTranscript, TranscriptSegment
from services import transcript_ingestion_service
from services.graph.connectors.mock_transcript_fixtures import INCIDENT_POSTMORTEM, STANDUP
from services.graph.transcript_payload_parser import parse_transcript_payload

_AUDIT_PATH = "services.llm_audit_service.LLMAuditService.log"
_VAULT_AUDIT_PATH = "services.vault_service._write_audit"
_HTTPX_POST = "httpx.post"


# AuditEvent.id is BigInteger, which (unlike plain Integer) SQLite does not
# treat as a ROWID alias, so it never auto-generates a value — a pre-existing
# schema detail that's harmless on production Postgres (BIGSERIAL) but trips
# up SQLite-backed tests. Test-only workaround; production code is unaffected.
@event.listens_for(AuditEvent, "before_insert")
def _assign_audit_event_id(mapper, connection, target):
    if target.id is None:
        target.id = (connection.execute(select(func.coalesce(func.max(AuditEvent.id), 0))).scalar() or 0) + 1


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # 64 hex chars = 32 bytes — a valid (test-only) AES-256-GCM key.
    monkeypatch.setattr("utils.config.settings.VAULT_MASTER_KEY", "11" * 32)

    try:
        yield session
    finally:
        session.close()


def _ollama_response(items: list[dict]) -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"message": {"content": json.dumps({"items": items})}}
    mock.raise_for_status = MagicMock()
    return mock


def _standup_transcript():
    return parse_transcript_payload(
        transcript_metadata=STANDUP["transcript_metadata"],
        vtt_content=STANDUP["vtt_content"],
        meeting_metadata=STANDUP["meeting_metadata"],
    )


def _postmortem_transcript():
    return parse_transcript_payload(
        transcript_metadata=INCIDENT_POSTMORTEM["transcript_metadata"],
        vtt_content=INCIDENT_POSTMORTEM["vtt_content"],
        meeting_metadata=INCIDENT_POSTMORTEM["meeting_metadata"],
    )


def test_ingest_creates_transcript_segments_and_knowledge_items(db):
    transcript = _standup_transcript()
    assert len(transcript.utterances) == 7  # sanity check on the fixture/parser

    mock_items = [
        {
            "knowledge_type": "commitment",
            "title": "Review PR #342",
            "speaker_token": "S1_SPEAKER_2",
            "owner_token": "S1_SPEAKER_1",
            "due_hint": "today",
            "confidence": 0.9,
            "extra_data": {},
        },
        {
            "knowledge_type": "github_pr",
            "title": "PR #342 payments-api",
            "speaker_token": "S1_SPEAKER_2",
            "confidence": 0.85,
            "extra_data": {
                "repo": "contoso/payments-api",
                "pr_number": "342",
                "pr_url": "https://github.com/contoso/payments-api/pull/342",
                "status_mentioned": "ready for review",
                "not_a_real_field": "should be stripped",
            },
        },
    ]

    with patch(_HTTPX_POST, return_value=_ollama_response(mock_items)) as mock_post, \
         patch(_AUDIT_PATH, return_value=99) as mock_log, \
         patch(_VAULT_AUDIT_PATH):
        result = transcript_ingestion_service.ingest(transcript, db)

    assert mock_post.called
    assert result["outcome"] == "ingested"
    assert result["knowledge_item_count"] == 2

    mt = db.query(MeetingTranscript).filter_by(id=result["meeting_transcript_id"]).first()
    assert mt is not None
    assert mt.meeting_id == "meeting-eng-standup-001"
    assert mt.content_hash is not None
    assert mt.segment_count == 7

    segments = db.query(TranscriptSegment).filter_by(transcript_id=mt.id).all()
    assert len(segments) == 7
    assert all(s.text_masked for s in segments)

    items = db.query(KnowledgeItem).filter_by(source_id=result["source_id"]).all()
    assert len(items) == 2
    pr_item = next(i for i in items if i.knowledge_type == "github_pr")
    assert pr_item.extra_data == {
        "repo": "contoso/payments-api",
        "pr_number": "342",
        "pr_url": "https://github.com/contoso/payments-api/pull/342",
        "status_mentioned": "ready for review",
    }
    assert all(i.llm_call_log_id == 99 for i in items)

    audit = db.query(AuditEvent).filter_by(source_id=result["source_id"], event_type="INGEST").first()
    assert audit is not None
    assert audit.outcome == "SUCCESS"
    assert mock_log.called


def test_quarantine_skips_llm_call(db):
    transcript = _postmortem_transcript()

    with patch(_HTTPX_POST) as mock_post, \
         patch(_AUDIT_PATH, return_value=1), \
         patch(_VAULT_AUDIT_PATH):
        result = transcript_ingestion_service.ingest(transcript, db)

    mock_post.assert_not_called()
    assert result["outcome"] == "quarantined"
    assert result["knowledge_item_count"] == 0

    mt = db.query(MeetingTranscript).filter_by(id=result["meeting_transcript_id"]).first()
    assert mt.sensitivity_label == "RESTRICTED"
    # Masking + storage still happen even when quarantined.
    assert db.query(TranscriptSegment).filter_by(transcript_id=mt.id).count() > 0
    assert db.query(KnowledgeItem).filter_by(source_id=result["source_id"]).count() == 0


def test_dedup_by_external_id_skips_reingestion(db):
    transcript = _standup_transcript()

    with patch(_HTTPX_POST, return_value=_ollama_response([])), \
         patch(_AUDIT_PATH, return_value=1), \
         patch(_VAULT_AUDIT_PATH):
        first = transcript_ingestion_service.ingest(transcript, db)

    transcript_again = _standup_transcript()  # same external_id
    with patch(_HTTPX_POST) as mock_post:
        second = transcript_ingestion_service.ingest(transcript_again, db)

    mock_post.assert_not_called()
    assert first["outcome"] == "ingested"
    assert second["outcome"] == "skipped_dup"


def test_idempotent_content_skips_reextraction_unless_forced(db):
    transcript = _standup_transcript()

    with patch(_HTTPX_POST, return_value=_ollama_response([{
            "knowledge_type": "action_item", "title": "Follow up with infra",
            "confidence": 0.7, "extra_data": {},
        }])), \
         patch(_AUDIT_PATH, return_value=42), \
         patch(_VAULT_AUDIT_PATH):
        first = transcript_ingestion_service.ingest(transcript, db)

    assert first["outcome"] == "ingested"
    # The patched LLMAuditService.log doesn't actually write a row (it has its
    # own independent session in production) — insert the row it WOULD have
    # written, so find_prior_successful_extraction has something to find.
    db.add(LLMCallLog(
        id=42, model="llama3.2", call_type="meeting_intelligence_extract",
        prompt_sha256="deadbeef", status="SUCCESS", source_id=first["source_id"],
    ))
    db.commit()

    # Same content, different transcript resource id (e.g. re-fetched under a
    # different Graph transcript id) — should be recognised as a duplicate.
    transcript_retry = _standup_transcript()
    transcript_retry.metadata.external_id = "transcript-eng-standup-001-RETRY"

    with patch(_HTTPX_POST) as mock_post_retry, \
         patch(_AUDIT_PATH, return_value=43), \
         patch(_VAULT_AUDIT_PATH):
        retry = transcript_ingestion_service.ingest(transcript_retry, db)

    mock_post_retry.assert_not_called()
    assert retry["outcome"] == "skipped_idempotent"
    assert retry["knowledge_item_count"] == 1

    # force_reextract bypasses the idempotency check entirely.
    transcript_forced = _standup_transcript()
    transcript_forced.metadata.external_id = "transcript-eng-standup-001-RETRY-2"

    with patch(_HTTPX_POST, return_value=_ollama_response([])) as mock_post_forced, \
         patch(_AUDIT_PATH, return_value=44), \
         patch(_VAULT_AUDIT_PATH):
        forced = transcript_ingestion_service.ingest(transcript_forced, db, force_reextract=True)

    mock_post_forced.assert_called_once()
    assert forced["outcome"] == "ingested"
