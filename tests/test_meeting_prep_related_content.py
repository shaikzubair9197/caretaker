"""
Tests for the Meeting Prep ↔ Content Retrieval integration (Document Retrieval
plan — Phase 2): build_snapshot() surfaces a ranked `related_content` list while
leaving the deterministic `documents` path untouched, and is fully fail-closed.

In-memory SQLite (StaticPool) so the retrieval audit's independent session shares
the test DB; the embedder is monkeypatched off.
"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import AuditEvent, Base, CalendarEvent, IndexedContent, SourceItem
from services import content_retrieval as cr
from services.meeting_prep_service import MeetingPrepService
from utils.time_utils import utcnow


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


_RAW = {
    "id": "evt-1",
    "subject": "Apollo launch sync",
    "body": {"contentType": "html", "content": "<p>Discuss Kubernetes deployment for PROJ-123</p>"},
    "organizer": {"emailAddress": {"name": "Org", "address": "org@x.com"}},
    "attendees": [{"emailAddress": {"name": "A", "address": "a@x.com"}, "status": {"response": "accepted"}}],
    "isOnlineMeeting": False,
}


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr("database.connection.SessionLocal", lambda: _ProxySession(session))
    monkeypatch.setattr("services.content_retrieval.encode", lambda text: None)
    cr.clear_cache()
    try:
        yield session
    finally:
        session.close()
        cr.clear_cache()


def _seed_event(db):
    start_at = utcnow() + timedelta(minutes=10)
    source = SourceItem(source_type="calendar", raw_text=json.dumps(_RAW),
                        metadata_={"external_id": _RAW["id"]})
    db.add(source)
    db.flush()
    event_row = CalendarEvent(
        source_id=source.id, external_id=_RAW["id"], subject_masked="<MASKED>",
        start_at=start_at, end_at=start_at + timedelta(minutes=60), is_cancelled=False,
    )
    db.add(event_row)
    db.flush()
    return event_row


def _seed_doc(db):
    row = IndexedContent(
        source_type="LOCAL", source_identifier="/docs/proj/apollo.txt",
        source_metadata={"root": "/docs", "relative_path": "proj/apollo.txt"},
        root_label="docs", filename="apollo_launch_plan.txt", folder="proj", folder_path="proj",
        extension=".txt", size_bytes=100, modified_at=utcnow(), content_hash="h1",
        keywords=["apollo", "launch", "kubernetes", "deployment"],
        entities=[{"type": "TICKET", "value": "PROJ-123"}],
        is_active=True, index_status="INDEXED", version=1, valid_from=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def test_related_content_present_and_documents_untouched(db):
    event_row = _seed_event(db)
    doc = _seed_doc(db)

    snap = MeetingPrepService.build_snapshot(db, event_row)
    assert snap is not None

    # related_content is a ranked list of typed documents.
    rc = snap["related_content"]
    assert isinstance(rc, list) and rc
    assert all(item["type"] == "document" for item in rc)
    assert doc.id in [item["content_id"] for item in rc]
    assert rc[0]["reasons"], "ranked result should carry explanation reasons"

    # The deterministic documents path is unchanged and still present.
    assert "documents" in snap
    assert set(snap["documents"]) == {"confidence", "items"}


def test_related_content_fail_closed(db, monkeypatch):
    event_row = _seed_event(db)
    _seed_doc(db)

    def _boom(*a, **k):
        raise RuntimeError("retrieval down")

    monkeypatch.setattr("services.content_retrieval.retrieve", _boom)

    snap = MeetingPrepService.build_snapshot(db, event_row)
    assert snap is not None                       # snapshot still builds
    assert snap["related_content"] == []          # fail-closed
    assert "documents" in snap                     # deterministic path unaffected
