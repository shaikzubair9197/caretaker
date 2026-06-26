"""
content_indexer — ties provider → processor → catalog (Document Retrieval plan —
Phase 1).

ContentIndexer (the SCAN side): enumerates candidates from a provider and
reconciles them into the catalog using only cheap stat/version signals — no file
bodies are read here (rule #2). initial_scan() additionally deactivates rows
whose source file has disappeared. reconcile_path() handles a single filesystem
event from the daemon.

ContentWorker (the WORK side): a poll/process loop with an explicit lifecycle —
initialize() / poll() / process() / shutdown() — so N workers (or a remote
worker) are a trivial extension (rule #7). It claims one queued item at a time
from the DB queue, reads + processes its bytes, and either finalises a brand-new
row in place or writes a version+1 supersession for a changed served row. Failures
become capped retry/backoff; a crash mid-item is reclaimed on the next startup.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from database.models import IndexedContent
from services import content_catalog
from services.content_processor import DocumentProcessor, ProcessingError
from services.content_providers import (
    ContentProvider,
    IndexedContentCandidate,
    LocalFolderProvider,
    _to_naive_utc,
)
from utils.config import settings
from utils.logger import get_logger

logger = get_logger("services.content_indexer")


def _default_session_factory():
    # Imported lazily so the daemon's load_dotenv() runs first and tests that
    # monkeypatch database.connection.SessionLocal are honoured.
    from database.connection import SessionLocal
    return SessionLocal()


def local_candidate_from_row(row: IndexedContent) -> IndexedContentCandidate:
    """Reconstruct a readable candidate from a catalog row for the WORK side.
    Uses the catalog's path-confined resolver; raises FileNotFoundError if the
    file has since been deleted/moved."""
    path = content_catalog.resolve_source_path(row)   # confined; raises on traversal
    stat = path.stat()                                # FileNotFoundError if gone
    return IndexedContentCandidate(
        source_type=row.source_type,
        source_identifier=row.source_identifier,
        source_metadata=row.source_metadata or {},
        root_label=row.root_label or "",
        filename=row.filename or path.name,
        folder=row.folder or "",
        folder_path=row.folder_path or "",
        extension=row.extension or path.suffix.lower(),
        size_bytes=stat.st_size,
        modified_at=_to_naive_utc(stat.st_mtime),
        _reader=lambda p=path: p.read_bytes(),
    )


# ── Scan side ─────────────────────────────────────────────────────────────────
class ContentIndexer:
    def __init__(self, session_factory=None, provider: Optional[ContentProvider] = None) -> None:
        self._session_factory = session_factory or _default_session_factory
        self._provider = provider or LocalFolderProvider()

    @property
    def provider(self) -> ContentProvider:
        return self._provider

    def initial_scan(self) -> dict:
        """Full reconcile of every candidate + deactivation of vanished rows.
        Returns a per-action summary. Each candidate is its own transaction so one
        bad entry never aborts the scan."""
        db = self._session_factory()
        actions: Counter = Counter()
        present: set[str] = set()
        try:
            for candidate in self._provider.iter_candidates():
                present.add(candidate.source_identifier)
                try:
                    action = content_catalog.reconcile_candidate(db, candidate)
                    db.commit()
                    actions[action] += 1
                except Exception as e:  # noqa: BLE001 - isolate one bad candidate
                    db.rollback()
                    actions["failed"] += 1
                    logger.warning(f"Reconcile failed for {candidate.source_identifier}: {e}")

            deactivated = content_catalog.mark_missing_inactive(db, self._provider.source_type, present)
            if deactivated:
                content_catalog.bump_generation(db)
            db.commit()
            actions["deactivated"] = deactivated
        finally:
            db.close()
        summary = dict(actions)
        logger.info(f"Initial scan complete: {summary} ({len(present)} present)")
        return summary

    def reconcile_path(self, path) -> Optional[str]:
        """Reconcile a single filesystem path (daemon event). Returns the action
        or None if the path is not an indexable candidate."""
        candidate = self._provider.candidate_for_path(path) if hasattr(self._provider, "candidate_for_path") else None
        if candidate is None:
            return None
        db = self._session_factory()
        try:
            action = content_catalog.reconcile_candidate(db, candidate)
            db.commit()
            logger.info(f"Reconciled {candidate.source_identifier} -> {action}")
            return action
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.warning(f"Reconcile failed for {path}: {e}")
            return None
        finally:
            db.close()


# ── Work side ─────────────────────────────────────────────────────────────────
class ContentWorker:
    """One processing worker with an explicit lifecycle. Owns a single long-lived
    session; the catalog claim functions commit internally, this worker commits
    each finalisation."""

    def __init__(
        self,
        session_factory=None,
        processor: Optional[DocumentProcessor] = None,
        max_retries: Optional[int] = None,
        backoff_seconds: Optional[int] = None,
    ) -> None:
        self._session_factory = session_factory or _default_session_factory
        self._processor = processor or DocumentProcessor()
        self._max_retries = max_retries if max_retries is not None else settings.CONTENT_MAX_INDEX_RETRIES
        self._backoff = backoff_seconds if backoff_seconds is not None else settings.CONTENT_RETRY_BACKOFF_SECONDS
        self._db = None

    def initialize(self) -> None:
        self._db = self._session_factory()
        content_catalog.reclaim_stale_indexing(self._db)
        self._db.commit()

    def poll(self) -> bool:
        """Claim and process ONE queued item. Returns True if work was done."""
        if self._db is None:
            self.initialize()
        row = content_catalog.claim_pending(self._db)
        mode = "new"
        if row is None:
            row = content_catalog.claim_reindex(self._db)
            mode = "reindex"
        if row is None:
            return False
        self.process(row, mode)
        return True

    def process(self, row: IndexedContent, mode: str) -> None:
        db = self._db
        try:
            candidate = local_candidate_from_row(row)
        except Exception as e:  # noqa: BLE001 - missing/traversal/bad metadata
            self._on_failure(row, mode, f"candidate build failed: {e}")
            return

        try:
            processed = self._processor.process(candidate)
        except ProcessingError as e:
            self._on_failure(row, mode, str(e))
            return
        except Exception as e:  # noqa: BLE001 - never let one item kill the loop
            self._on_failure(row, mode, f"unexpected processing error: {e}")
            return

        try:
            if mode == "new":
                content_catalog.finalize_new(db, row, processed, candidate)
                content_catalog.bump_generation(db)
            else:
                new_row = content_catalog.supersede_with_reindex(db, row, processed, candidate)
                if new_row is not None:
                    content_catalog.bump_generation(db)
            db.commit()
        except Exception as e:  # noqa: BLE001
            self._on_failure(row, mode, f"finalise failed: {e}")

    def _on_failure(self, row: IndexedContent, mode: str, message: str) -> None:
        db = self._db
        db.rollback()
        if mode == "new":
            # Brand-new/retried row is not served — apply capped retry/backoff.
            content_catalog.mark_error(db, row, message, self._max_retries, self._backoff)
            db.commit()
        else:
            # The served row is untouched and still valid — leave it serving the
            # prior version; the next full scan re-flags if the file still differs.
            logger.warning(f"Reindex failed for id={row.id} file={row.filename}: {message}; served row left intact")

    def run_until_idle(self) -> int:
        """Drain the queue (used by the daemon between sleeps and by tests)."""
        processed = 0
        while self.poll():
            processed += 1
        return processed

    def shutdown(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            finally:
                self._db = None
