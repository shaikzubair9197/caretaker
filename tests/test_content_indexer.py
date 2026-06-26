"""
Tests for content_indexer — ContentIndexer (scan) + ContentWorker (work) over a
real LocalFolderProvider and DocumentProcessor against a temp MeetingDocs folder
(Document Retrieval plan — Phase 1). In-memory SQLite (StaticPool); the embedder
is monkeypatched.

Covers the full Phase-1 lifecycle: initial scan enqueues, the worker indexes to
served, a content change drives a version+1 supersession while staying served, a
deletion deactivates, and a processing failure becomes a capped ERROR.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, IndexedContent
from services import content_catalog as cat
from services.content_indexer import ContentIndexer, ContentWorker
from services.content_processor import ProcessingError
from services.content_providers import LocalFolderProvider
from utils.config import settings


class _ProxySession:
    """Funnel every session opened by the indexer/worker into the test's single
    session (commit->flush, close/rollback noop) so they share one in-memory DB —
    same pattern as tests/test_secure_store_service.py."""

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
def env(tmp_path, monkeypatch):
    root = tmp_path / "MeetingDocs"
    (root / "docs").mkdir(parents=True)
    (root / "notes").mkdir(parents=True)
    (root / "docs" / "spec.txt").write_text("Kubernetes deployment plan PROJ-1\n")
    (root / "notes" / "readme.md").write_text("# Readme\n\nNotes about Redis\n")

    monkeypatch.setattr(settings, "MEETING_DOCS_ROOTS", [str(root)])
    monkeypatch.setattr(settings, "CONTENT_TEXT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr("services.content_processor.encode", lambda text: [0.1, 0.2, 0.3])

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    factory = lambda: _ProxySession(session)  # noqa: E731

    try:
        yield {"root": root, "session": session, "factory": factory}
    finally:
        session.close()


def _active(session, filename):
    return (
        session.query(IndexedContent)
        .filter(IndexedContent.filename == filename, IndexedContent.is_active.is_(True))
        .first()
    )


def test_initial_scan_enqueues(env):
    indexer = ContentIndexer(session_factory=env["factory"], provider=LocalFolderProvider())
    summary = indexer.initial_scan()
    assert summary.get("new") == 2
    assert cat.counts_by_status(env["session"]).get("PENDING") == 2


def test_worker_indexes_to_served(env):
    session = env["session"]
    indexer = ContentIndexer(session_factory=env["factory"], provider=LocalFolderProvider())
    worker = ContentWorker(session_factory=env["factory"])

    indexer.initial_scan()
    gen_before = cat.read_generation(session)
    processed = worker.run_until_idle()

    assert processed == 2
    served = cat.list_served(session)
    assert len(served) == 2
    assert all(r.index_status == "INDEXED" for r in served)
    assert all(r.embedding == [0.1, 0.2, 0.3] for r in served)
    assert cat.read_generation(session) > gen_before
    worker.shutdown()


def test_change_drives_version_supersession_while_served(env):
    session = env["session"]
    indexer = ContentIndexer(session_factory=env["factory"], provider=LocalFolderProvider())
    worker = ContentWorker(session_factory=env["factory"])

    indexer.initial_scan()
    worker.run_until_idle()
    v1 = _active(session, "spec.txt")
    assert v1.version == 1

    # Change the file's content (different size + mtime).
    (env["root"] / "docs" / "spec.txt").write_text(
        "Kubernetes and Postgres migration plan PROJ-2 with additional detail\n"
    )
    indexer.initial_scan()
    flagged = _active(session, "spec.txt")
    assert flagged.needs_reindex is True
    assert len(cat.list_served(session)) == 2          # still served while flagged

    worker.run_until_idle()
    v2 = _active(session, "spec.txt")
    assert v2.version == 2
    assert v2.id != v1.id
    assert len(cat.list_served(session)) == 2          # exactly one active per file
    old = session.get(IndexedContent, v1.id)
    assert old.is_active is False
    assert old.superseded_by_id == v2.id
    worker.shutdown()


def test_deletion_deactivates(env):
    session = env["session"]
    indexer = ContentIndexer(session_factory=env["factory"], provider=LocalFolderProvider())
    worker = ContentWorker(session_factory=env["factory"])

    indexer.initial_scan()
    worker.run_until_idle()
    assert len(cat.list_served(session)) == 2

    (env["root"] / "notes" / "readme.md").unlink()
    indexer.initial_scan()

    served = cat.list_served(session)
    assert len(served) == 1
    assert served[0].filename == "spec.txt"
    worker.shutdown()


def test_processing_failure_caps_to_error(env):
    session = env["session"]

    class _RaisingProcessor:
        def process(self, candidate):
            raise ProcessingError("stub failure")

    indexer = ContentIndexer(session_factory=env["factory"], provider=LocalFolderProvider())
    worker = ContentWorker(session_factory=env["factory"], processor=_RaisingProcessor(),
                           max_retries=1, backoff_seconds=0)

    indexer.initial_scan()
    worker.run_until_idle()

    statuses = {r.index_status for r in session.query(IndexedContent).all()}
    assert statuses == {"ERROR"}
    assert cat.list_served(session) == []
    worker.shutdown()
