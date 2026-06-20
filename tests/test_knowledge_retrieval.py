"""
Plan 2 verification tests — knowledge retrieval, versioning & intelligence.

Covers the 7 verification scenarios from the Plan 2 blueprint:
  1. Credential supersession (new value -> old deactivated, new is version 2).
  2. Same-key invariant: at most one active row per knowledge_key (partial
     unique index exercised) + concurrent-style double resolve collapses to one.
  3. Duplicate confirmation: no new row, confidence bumped, embedding untouched.
  4. Secret retrieval returns a vault_token reference only + a RETRIEVAL audit.
  5. Source-precedence tie-break on equal-confidence candidates.
  6. Ambiguous-band conflict surfaces both candidates with conflict_flag=True.
  7. Retrieval results carry populated source_type / source_id for citations.

Uses an in-memory SQLite DB (StaticPool so the independent audit session shares
it). The embedder and Ollama are never hit for real — embedding vectors are
injected and LLM calls are patched, same convention as the Plan 1 tests.

Run with:
    cd caretaker
    python -m pytest tests/test_knowledge_retrieval.py -v
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import crud
from database.models import AuditEvent, Base, KnowledgeItem
from embeddings.vector_search import _cosine_similarity  # noqa: F401  (sanity import)
from services import knowledge_evolution_service as evo
from services import knowledge_retrieval_service as retrieval
from services.knowledge_intent_service import KnowledgeIntent
from services.llm_service import LLMCallResult, LLMStatus
from utils.time_utils import utcnow


# AuditEvent.id is BigInteger — SQLite won't auto-assign it (same workaround as
# tests/test_transcript_ingestion.py).
@event.listens_for(AuditEvent, "before_insert")
def _assign_audit_event_id(mapper, connection, target):
    if target.id is None:
        target.id = (connection.execute(select(func.coalesce(func.max(AuditEvent.id), 0))).scalar() or 0) + 1


class _ProxySession:
    """Wraps the test session so the retrieval/vault audit writers — which open
    their own SessionLocal — write into the SAME in-memory DB the test reads,
    without closing the test's session. commit() becomes flush(); close() noop."""

    def __init__(self, real):
        self._real = real

    def add(self, obj):
        self._real.add(obj)

    def commit(self):
        self._real.flush()

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Independent-session audit writers (retrieval RETRIEVAL audit) -> test DB.
    monkeypatch.setattr("database.connection.SessionLocal", lambda: _ProxySession(session))

    try:
        yield session
    finally:
        session.close()


def _mk_item(db, knowledge_type, title, key, **kw):
    item = KnowledgeItem(
        source_id=kw.get("source_id"),
        knowledge_type=knowledge_type,
        title_masked=title,
        detail_masked=kw.get("detail"),
        owner_token=kw.get("owner_token"),
        confidence=kw.get("confidence", 0.9),
        status="open",
        source_type=kw.get("source_type", "transcript"),
        embedding=kw.get("embedding"),
        extra_data=kw.get("extra_data"),
        knowledge_key=key,
        version=kw.get("version", 1),
        is_active=kw.get("is_active", True),
        valid_from=kw.get("valid_from", utcnow()),
        resolution_method=kw.get("resolution_method", "new_chain"),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _credential_row(db, vault_token, title, source_id):
    """Unresolved credential_reference row (as persist would create it) ready for
    evolution.resolve_item."""
    row = KnowledgeItem(
        source_id=source_id,
        knowledge_type="credential_reference",
        title_masked=title,
        detail_masked=None,
        confidence=0.9,
        status="open",
        source_type="transcript",
        extra_data={"system_name": "openai", "credential_kind": "api_key", "vault_token": vault_token},
    )
    db.add(row)
    return row


# ── 1. Credential supersession ───────────────────────────────────────────────

def test_credential_key_rotation_supersedes_old_version(db):
    with patch("embeddings.embedder.encode", return_value=[0.1, 0.2, 0.3]):
        a = _credential_row(db, "API_KEY_1", "OpenAI key (old)", source_id=1)
        evo.resolve_item(a, db)
        db.commit()

        b = _credential_row(db, "API_KEY_2", "OpenAI key (rotated)", source_id=2)
        evo.resolve_item(b, db)
        db.commit()

    db.refresh(a)
    db.refresh(b)

    assert a.knowledge_key == b.knowledge_key == "credential_reference:openai:api_key"
    assert a.is_active is False
    assert a.valid_to is not None
    assert a.superseded_by_id == b.id
    assert b.is_active is True
    assert b.version == 2
    assert b.resolution_method == "supersedes"

    active = crud.get_active_knowledge_by_key(db, a.knowledge_key)
    assert active.id == b.id


# ── 2. Single active version per key (invariant + DB-level backstop) ──────────

def test_partial_unique_index_blocks_two_active_per_key(db):
    from sqlalchemy.exc import IntegrityError

    _mk_item(db, "decision", "Decision A", key="dup-key", is_active=True)
    with pytest.raises(IntegrityError):
        _mk_item(db, "decision", "Decision B", key="dup-key", is_active=True)
    db.rollback()


def test_double_resolve_same_key_leaves_one_active(db):
    with patch("embeddings.embedder.encode", return_value=[0.5, 0.5, 0.5]):
        a = _credential_row(db, "K1", "key v1", source_id=1)
        evo.resolve_item(a, db)
        b = _credential_row(db, "K2", "key v2", source_id=2)
        evo.resolve_item(b, db)
        db.commit()

    actives = crud.list_active_knowledge_by_key(db, "credential_reference:openai:api_key")
    assert len(actives) == 1


# ── 3. Duplicate confirmation: no new row, confidence up, embedding unchanged ──

def test_duplicate_confirmation_bumps_confidence_without_new_row_or_embedding(db):
    with patch("embeddings.embedder.encode", return_value=[0.9, 0.1, 0.0]):
        a = _credential_row(db, "SAME", "key", source_id=1)
        evo.resolve_item(a, db)
        db.commit()

    db.refresh(a)
    original_embedding = list(a.embedding)
    original_conf = float(a.confidence)
    key = a.knowledge_key

    # Re-mention identical content. encode must NOT be called for a duplicate
    # confirmation (embedding-regeneration gating, Plan 2 §5).
    with patch("embeddings.embedder.encode", side_effect=AssertionError("encode must not run")):
        b = _credential_row(db, "SAME", "key", source_id=2)
        method = evo.resolve_item(b, db)
        db.commit()

    assert method == "duplicate_confirmation"
    rows = db.query(KnowledgeItem).filter(KnowledgeItem.knowledge_key == key).all()
    assert len(rows) == 1                       # no new row
    survivor = rows[0]
    assert survivor.id == a.id
    assert float(survivor.confidence) > original_conf  # bumped
    assert list(survivor.embedding) == original_embedding  # untouched


# ── 4. Secret retrieval returns vault_token reference only + RETRIEVAL audit ──

def test_secret_retrieval_returns_vault_reference_and_writes_audit(db):
    _mk_item(
        db, "credential_reference", "OpenAI API key", key="credential_reference:openai:api_key",
        embedding=[1.0, 0.0, 0.0], extra_data={"system_name": "openai", "credential_kind": "api_key",
                                               "vault_token": "API_KEY_7"}, source_id=3,
    )

    intent = KnowledgeIntent(
        raw_query="what's the latest openai api key",
        masked_query="what's the latest openai api key",
        query_type="secret",
        is_secret_request=True,
        knowledge_types=["credential_reference"],
        classifier_status="SUCCESS",
    )

    with patch("services.knowledge_intent_service.classify", return_value=intent), \
         patch("embeddings.embedder.encode", return_value=[1.0, 0.0, 0.0]):
        out = retrieval.retrieve("what's the latest openai api key", db)

    assert out["status"] == "ok"
    assert len(out["results"]) == 1
    res = out["results"][0]
    assert res["value_ref"] == "vault_token:API_KEY_7"   # reference only
    assert "API_KEY_7" in res["value_ref"]
    assert "sk-" not in res["value_ref"]                  # never a decrypted value

    audit = db.query(AuditEvent).filter(AuditEvent.event_type == "RETRIEVAL").first()
    assert audit is not None
    assert audit.resource_type == "KnowledgeItem"
    assert audit.event_data["is_secret_request"] is True


# ── 5. Source-precedence tie-break ───────────────────────────────────────────

def test_source_precedence_breaks_confidence_tie(db):
    # Two equal-confidence, equally-similar decisions from different sources.
    _mk_item(db, "decision", "Ship on Friday", key="dec-email",
             confidence=0.9, source_type="email", embedding=[1.0, 0.0], source_id=10)
    _mk_item(db, "decision", "Ship on Friday", key="dec-transcript",
             confidence=0.9, source_type="transcript", embedding=[1.0, 0.0], source_id=11)

    intent = KnowledgeIntent(
        raw_query="when do we ship", masked_query="when do we ship",
        query_type="semantic", knowledge_types=["decision"], classifier_status="SUCCESS",
    )
    with patch("services.knowledge_intent_service.classify", return_value=intent), \
         patch("embeddings.embedder.encode", return_value=[1.0, 0.0]):
        out = retrieval.retrieve("when do we ship", db)

    assert out["status"] == "ok"
    # transcript (precedence 80) must outrank email (50) on the tie.
    assert out["results"][0]["source_type"] == "transcript"


# ── 6. Ambiguous-band conflict surfaces both candidates ──────────────────────

def test_ambiguous_band_conflict_flags_both_candidates(db):
    _mk_item(db, "decision", "We will use Postgres", key="dec-1",
             confidence=0.9, source_type="transcript", embedding=[1.0, 0.0], source_id=20)
    _mk_item(db, "decision", "We will use MySQL", key="dec-2",
             confidence=0.9, source_type="transcript", embedding=[0.8, 0.6], source_id=21)

    intent = KnowledgeIntent(
        raw_query="which database did we pick", masked_query="which database did we pick",
        query_type="semantic", knowledge_types=["decision"], classifier_status="SUCCESS",
    )
    conflict_result = LLMCallResult(data={"relationship": "CONFLICT", "confidence": 0.6},
                                    status=LLMStatus.SUCCESS)

    with patch("services.knowledge_intent_service.classify", return_value=intent), \
         patch("embeddings.embedder.encode", return_value=[0.95, 0.31]), \
         patch("services.llm_service.LLMService.adjudicate_knowledge_relationship",
               return_value=conflict_result) as mock_adj:
        out = retrieval.retrieve("which database did we pick", db)

    assert mock_adj.called                       # adjudication was invoked
    assert out["conflict"] is True
    assert len(out["results"]) == 2
    assert all(r["conflict_flag"] is True for r in out["results"])


# ── 7. Citation fields populated ─────────────────────────────────────────────

def test_results_include_source_type_and_source_id_for_citation(db):
    _mk_item(db, "github_pr", "PR #342", key="github_pr:contoso/payments-api:342",
             confidence=0.9, source_type="transcript", embedding=[1.0, 0.0], source_id=42,
             extra_data={"repo": "contoso/payments-api", "pr_number": "342"})

    intent = KnowledgeIntent(
        raw_query="status of PR 342", masked_query="status of PR 342",
        query_type="structured", knowledge_types=["github_pr"], classifier_status="SUCCESS",
    )
    with patch("services.knowledge_intent_service.classify", return_value=intent), \
         patch("embeddings.embedder.encode", return_value=[1.0, 0.0]):
        out = retrieval.retrieve("status of PR 342", db)

    assert out["status"] == "ok"
    res = out["results"][0]
    assert res["source_type"] == "transcript"
    assert res["source_id"] == 42
    assert res["knowledge_key"] == "github_pr:contoso/payments-api:342"
    assert res["version"] == 1


# ── Bonus: intent heuristic guards a secret query when the LLM is down ────────

def test_intent_heuristic_flags_secret_when_llm_unavailable(db):
    from services import knowledge_intent_service

    failed = LLMCallResult(data=None, status=LLMStatus.CONNECTION_REFUSED)
    with patch("services.knowledge_intent_service.LLMService.classify_knowledge_query",
               return_value=failed), \
         patch("services.knowledge_intent_service.PreprocessingService.mask_pii",
               return_value=("what is the openai api key", [])):
        intent = knowledge_intent_service.classify("what is the openai api key")

    assert intent.classifier_status == "FALLBACK"
    assert intent.is_secret_request is True
    assert intent.query_type == "secret"


# ── Confidence floor returns insufficient-confidence rather than a guess ──────

def test_low_confidence_only_returns_insufficient(db):
    _mk_item(db, "decision", "Vague maybe", key="dec-low",
             confidence=0.1, source_type="email", embedding=[1.0, 0.0], source_id=50)

    intent = KnowledgeIntent(
        raw_query="what was decided", masked_query="what was decided",
        query_type="semantic", knowledge_types=["decision"], classifier_status="SUCCESS",
    )
    # retrieval score low + low extraction -> below floor.
    with patch("services.knowledge_intent_service.classify", return_value=intent), \
         patch("embeddings.embedder.encode", return_value=[0.0, 1.0]):
        out = retrieval.retrieve("what was decided", db)

    assert out["status"] == "insufficient_confidence"
    assert out["results"] == []
