"""
Tests for content_catalog — the DB-owning layer of the Content Retrieval catalog
(Document Retrieval plan — Phase 1). In-memory SQLite (StaticPool).

Covers: enqueue of a new candidate, the two claim sources (PENDING + reindex),
finalise-in-place, change detection, version+1 supersession with continuous
availability, the partial-unique-on-active invariant, false-positive reindex
no-op, capped retry/backoff, stale-INDEXING reclaim, missing-row deactivation,
the generation counter, and path-confined source resolution.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, IndexedContent
from services import content_catalog as cat
from services.content_processor import ProcessedDocument
from services.content_providers import IndexedContentCandidate

_T0 = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _candidate(identifier="/docs/a.txt", filename="a.txt", folder="docs",
               size=100, mtime=_T0, root="/docs", rel="a.txt"):
    return IndexedContentCandidate(
        source_type="LOCAL",
        source_identifier=identifier,
        source_metadata={"root": root, "relative_path": rel},
        root_label="docs",
        filename=filename,
        folder=folder,
        folder_path="",
        extension=".txt",
        size_bytes=size,
        modified_at=mtime,
    )


def _processed(content_hash="hash-1", keywords=None, embedding=None):
    return ProcessedDocument(
        content_hash=content_hash,
        text_cache_path=f"{content_hash}.txt",
        char_count=10,
        keywords=keywords or ["alpha"],
        entities=[],
        embedding=embedding,
        embedding_model="all-MiniLM-L6-v2" if embedding else None,
        embedding_dimension=len(embedding) if embedding else None,
        parser_version="1",
        pipeline_version="1",
        text_extracted_at=_T0,
        embedded_at=_T0 if embedding else None,
    )


def test_reconcile_new_inserts_pending_active(db):
    assert cat.reconcile_candidate(db, _candidate()) == "new"
    row = cat.get_active(db, "LOCAL", "/docs/a.txt")
    assert row is not None
    assert row.index_status == "PENDING"
    assert row.is_active is True
    assert row.version == 1
    assert cat.list_served(db) == []          # PENDING is not served


def test_claim_pending_then_finalise_serves_and_bumps_generation(db):
    cat.reconcile_candidate(db, _candidate())
    db.commit()
    gen0 = cat.read_generation(db)

    claimed = cat.claim_pending(db)
    assert claimed is not None
    assert claimed.index_status == "INDEXING"
    assert cat.claim_pending(db) is None       # nothing else queued

    cat.finalize_new(db, claimed, _processed(embedding=[0.1, 0.2]), _candidate())
    cat.bump_generation(db)
    db.commit()

    served = cat.list_served(db)
    assert len(served) == 1
    assert served[0].index_status == "INDEXED"
    assert cat.read_generation(db) == gen0 + 1


def _indexed_row(db, embedding=None):
    cat.reconcile_candidate(db, _candidate())
    row = cat.claim_pending(db)
    cat.finalize_new(db, row, _processed(embedding=embedding), _candidate())
    db.commit()
    return cat.get_active(db, "LOCAL", "/docs/a.txt")


def test_reconcile_unchanged_is_noop(db):
    _indexed_row(db)
    assert cat.reconcile_candidate(db, _candidate()) == "unchanged"
    assert cat.get_active(db, "LOCAL", "/docs/a.txt").needs_reindex is False


def test_reconcile_changed_flags_reindex(db):
    _indexed_row(db)
    # Different size + mtime => change detected.
    changed = _candidate(size=200, mtime=_T0 + timedelta(hours=1))
    assert cat.reconcile_candidate(db, changed) == "reindex"
    assert cat.get_active(db, "LOCAL", "/docs/a.txt").needs_reindex is True


def test_claim_reindex_keeps_row_served_then_supersedes(db):
    _indexed_row(db)
    cat.reconcile_candidate(db, _candidate(size=200, mtime=_T0 + timedelta(hours=1)))
    db.commit()

    served_before = cat.list_served(db)
    assert len(served_before) == 1            # still served while flagged

    claimed = cat.claim_reindex(db)
    assert claimed is not None
    assert claimed.needs_reindex is False
    # Still served while the worker reprocesses (continuous availability).
    assert len(cat.list_served(db)) == 1

    new_row = cat.supersede_with_reindex(
        db, claimed, _processed(content_hash="hash-2"),
        _candidate(size=200, mtime=_T0 + timedelta(hours=1)),
    )
    cat.bump_generation(db)
    db.commit()

    assert new_row is not None
    assert new_row.version == 2
    served = cat.list_served(db)
    assert len(served) == 1                   # exactly one active version
    assert served[0].id == new_row.id
    old = db.get(IndexedContent, claimed.id)
    assert old.is_active is False
    assert old.superseded_by_id == new_row.id


def test_reindex_false_positive_is_noop(db):
    row = _indexed_row(db)
    # Same hash + versions => no real change; supersede returns None, no v2.
    result = cat.supersede_with_reindex(db, row, _processed(content_hash="hash-1"), _candidate())
    db.commit()
    assert result is None
    assert cat.get_active(db, "LOCAL", "/docs/a.txt").version == 1
    assert len(cat.list_served(db)) == 1


def test_partial_unique_on_active(db):
    _indexed_row(db)
    # A second active row for the same source must violate the partial unique index.
    dup = IndexedContent(
        source_type="LOCAL", source_identifier="/docs/a.txt", filename="a.txt",
        is_active=True, index_status="INDEXED", version=1,
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_mark_error_retry_then_cap(db):
    cat.reconcile_candidate(db, _candidate())
    row = cat.claim_pending(db)

    cat.mark_error(db, row, "boom", max_retries=2, backoff_seconds=30)
    db.commit()
    assert row.index_status == "PENDING"      # below cap -> requeued
    assert row.retry_count == 1
    assert row.next_retry_at is not None

    cat.mark_error(db, row, "boom again", max_retries=2, backoff_seconds=30)
    db.commit()
    assert row.index_status == "ERROR"        # at cap -> terminal
    assert row.next_retry_at is None


def test_backoff_excludes_row_from_claim(db):
    cat.reconcile_candidate(db, _candidate())
    row = cat.claim_pending(db)
    cat.mark_error(db, row, "boom", max_retries=5, backoff_seconds=300)
    db.commit()
    # next_retry_at is in the future, so the row is not claimable yet.
    assert cat.claim_pending(db) is None


def test_reclaim_stale_indexing(db):
    cat.reconcile_candidate(db, _candidate())
    row = cat.claim_pending(db)               # -> INDEXING (simulated crash mid-item)
    assert row.index_status == "INDEXING"
    reclaimed = cat.reclaim_stale_indexing(db)
    db.commit()
    assert reclaimed == 1
    assert cat.get_active(db, "LOCAL", "/docs/a.txt").index_status == "PENDING"


def test_mark_missing_inactive(db):
    _indexed_row(db)
    deactivated = cat.mark_missing_inactive(db, "LOCAL", present_identifiers=set())
    db.commit()
    assert deactivated == 1
    assert cat.list_served(db) == []
    assert cat.get_active(db, "LOCAL", "/docs/a.txt") is None


def test_generation_counter(db):
    assert cat.read_generation(db) == 0
    assert cat.bump_generation(db) == 1
    assert cat.bump_generation(db) == 2
    db.commit()
    assert cat.read_generation(db) == 2


def test_resolve_source_path_confined(tmp_path, db):
    root = tmp_path / "MeetingDocs"
    (root / "sub").mkdir(parents=True)
    target = root / "sub" / "a.txt"
    target.write_text("hello")

    row = IndexedContent(
        source_type="LOCAL", source_identifier=str(target), filename="a.txt",
        source_metadata={"root": str(root), "relative_path": "sub/a.txt"},
        is_active=True, index_status="INDEXED",
    )
    db.add(row)
    db.flush()
    assert cat.resolve_source_path(row) == target.resolve()


def test_resolve_source_path_rejects_traversal(tmp_path, db):
    root = tmp_path / "MeetingDocs"
    root.mkdir()
    row = IndexedContent(
        source_type="LOCAL", source_identifier="x", filename="x",
        source_metadata={"root": str(root), "relative_path": "../../etc/passwd"},
        is_active=True, index_status="INDEXED",
    )
    db.add(row)
    db.flush()
    with pytest.raises(PermissionError):
        cat.resolve_source_path(row)
