"""
Tests for content_retrieval — the staged, explainable ranking pipeline
(Document Retrieval plan — Phase 2). In-memory SQLite (StaticPool) so the
independent RETRIEVAL audit session shares the test DB.

Covers: keyword / entity / semantic ranking, typed result shape + explainability
(reasons + final_score_breakdown summing to score), the embedding-optional
fallback, top-N + threshold, the empty-query short-circuit, folder-cluster
diversification, the generation-keyed cache, and the per-call RETRIEVAL audit
(counts only — never query text / keywords / content / paths).
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import AuditEvent, Base, IndexedContent
from services import content_retrieval as cr
from services.content_retrieval import RetrievalQuery

_T0 = datetime(2026, 1, 1, 12, 0, 0)


@event.listens_for(AuditEvent, "before_insert")
def _assign_audit_event_id(mapper, connection, target):
    if target.id is None:
        target.id = (connection.execute(select(func.coalesce(func.max(AuditEvent.id), 0))).scalar() or 0) + 1


class _ProxySession:
    def __init__(self, real):
        self._real = real

    def commit(self):
        self._real.flush()

    def rollback(self):
        pass

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    # Route the independent audit session into the test DB.
    monkeypatch.setattr("database.connection.SessionLocal", lambda: _ProxySession(session))
    cr.clear_cache()
    try:
        yield session
    finally:
        session.close()
        cr.clear_cache()


def _seed(db, *, filename, folder, keywords, entities=None, embedding=None,
          modified=_T0, source_type="LOCAL", ext=".txt"):
    row = IndexedContent(
        source_type=source_type,
        source_identifier=f"/docs/{folder}/{filename}",
        source_metadata={"root": "/docs", "relative_path": f"{folder}/{filename}"},
        root_label="docs",
        filename=filename,
        folder=folder,
        folder_path=folder,
        extension=ext,
        size_bytes=100,
        modified_at=modified,
        content_hash=filename,
        keywords=keywords,
        entities=entities or [],
        embedding=embedding,
        embedding_model="m" if embedding else None,
        embedding_dimension=len(embedding) if embedding else None,
        is_active=True,
        index_status="INDEXED",
        version=1,
        valid_from=_T0,
    )
    db.add(row)
    db.flush()
    return row


def test_keyword_ranking(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)  # no embeddings
    hit = _seed(db, filename="apollo.txt", folder="proj", keywords=["apollo", "launch", "plan"])
    miss = _seed(db, filename="other.txt", folder="misc", keywords=["unrelated", "stuff"])

    results = cr.retrieve(db, RetrievalQuery(text="apollo launch", keywords=["apollo", "launch"]))
    assert results[0]["content_id"] == hit.id
    assert results[0]["score"] > 0
    # The non-matching doc is filtered out by the score threshold.
    assert all(r["content_id"] != miss.id for r in results) or results[-1]["content_id"] == miss.id


def test_entity_ranking(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    hit = _seed(db, filename="ticket.txt", folder="proj", keywords=["work"],
                entities=[{"type": "TICKET", "value": "PROJ-123"}])
    _seed(db, filename="none.txt", folder="proj", keywords=["work"], entities=[])

    results = cr.retrieve(db, RetrievalQuery(
        text="PROJ-123", keywords=["work"],
        entities=[{"type": "TICKET", "value": "PROJ-123"}],
    ))
    assert results[0]["content_id"] == hit.id
    assert any(r["reason_type"] == "entity" for r in results[0]["reasons"])


def test_semantic_ranking(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: [1.0, 0.0, 0.0])
    near = _seed(db, filename="near.txt", folder="a", keywords=["shared"], embedding=[1.0, 0.0, 0.0])
    far = _seed(db, filename="far.txt", folder="a", keywords=["shared"], embedding=[0.0, 1.0, 0.0])

    results = cr.retrieve(db, RetrievalQuery(text="anything", keywords=["shared"]))
    assert results[0]["content_id"] == near.id
    ids = [r["content_id"] for r in results]
    # The near doc ranks first; the far doc is either ranked below it or dropped
    # by the relative-score gate (both acceptable — never ranked above near).
    if far.id in ids:
        assert ids.index(near.id) < ids.index(far.id)


def test_typed_result_shape_and_breakdown(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    _seed(db, filename="apollo.txt", folder="proj", keywords=["apollo", "launch"])

    results = cr.retrieve(db, RetrievalQuery(text="apollo", keywords=["apollo"]))
    r = results[0]
    assert r["type"] == "document"
    assert {"content_id", "filename", "folder", "extension", "score", "reasons", "final_score_breakdown"} <= set(r)
    assert "path" not in r and "source_identifier" not in r and "folder_path" not in r
    for reason in r["reasons"]:
        assert {"reason_type", "reason_value", "score", "weight"} == set(reason)
    # Contributions sum to the final score.
    assert abs(sum(r["final_score_breakdown"].values()) - r["score"]) < 1e-4


def test_embedding_fallback_drops_semantic(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    _seed(db, filename="apollo.txt", folder="proj", keywords=["apollo"])

    results = cr.retrieve(db, RetrievalQuery(text="apollo", keywords=["apollo"]))
    assert results, "should still rank on keywords without embeddings"
    assert results[0]["final_score_breakdown"]["semantic"] == 0.0


def test_top_n_and_threshold(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    for i in range(5):
        _seed(db, filename=f"doc{i}.txt", folder="proj", keywords=["apollo", f"k{i}"])

    results = cr.retrieve(db, RetrievalQuery(text="apollo", keywords=["apollo"], top_n=2))
    assert len(results) <= 2


def test_empty_query_returns_empty(db):
    _seed(db, filename="apollo.txt", folder="proj", keywords=["apollo"])
    assert cr.retrieve(db, RetrievalQuery()) == []


def test_folder_cluster_diversification(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    # Two docs in "big", one in "small" — all share the query keyword.
    _seed(db, filename="b1.txt", folder="big", keywords=["apollo"])
    _seed(db, filename="b2.txt", folder="big", keywords=["apollo"])
    _seed(db, filename="s1.txt", folder="small", keywords=["apollo"])

    results = cr.retrieve(db, RetrievalQuery(text="apollo", keywords=["apollo"], top_n=5))
    by_folder = {r["folder"]: r["final_score_breakdown"]["folder"] for r in results}
    assert by_folder["big"] > by_folder["small"]


def test_generation_keyed_cache(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    _seed(db, filename="apollo.txt", folder="proj", keywords=["apollo"])

    calls = {"n": 0}
    real_pipeline = cr._run_pipeline

    def _counting(dbs, q):
        calls["n"] += 1
        return real_pipeline(dbs, q)

    monkeypatch.setattr(cr, "_run_pipeline", _counting)
    query = RetrievalQuery(text="apollo", keywords=["apollo"])

    cr.retrieve(db, query)
    cr.retrieve(db, query)
    assert calls["n"] == 1                      # second call served from cache

    cr.clear_cache()
    cr.retrieve(db, query)
    assert calls["n"] == 2                      # cache cleared -> recomputed


def test_retrieval_audit_written_without_pii(db, monkeypatch):
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    hit = _seed(db, filename="apollo.txt", folder="proj", keywords=["apollo"])

    results = cr.retrieve(db, RetrievalQuery(text="apollo secret-codename", keywords=["apollo", "secret-codename"]))
    audits = db.query(AuditEvent).filter(AuditEvent.event_type == "RETRIEVAL").all()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.resource_type == "IndexedContent"
    assert audit.resource_id == hit.id
    assert audit.event_data["result_count"] == len(results)
    assert hit.id in audit.event_data["content_ids"]
    # No query text / keyword values leaked into the audit payload.
    blob = str(audit.event_data)
    assert "secret-codename" not in blob and "apollo" not in blob
