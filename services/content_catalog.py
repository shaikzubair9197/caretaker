"""
content_catalog — the ONLY database access for the Content Retrieval catalog
(Document Retrieval plan — Phase 1). Owns IndexedContent + content_index_state.

Two orthogonal lifecycles (design rule #3):
  • Work state  — index_status PENDING|INDEXING|INDEXED|ERROR (+ needs_reindex).
  • Serve state — is_active AND index_status='INDEXED'.
Retrieval reads only the served set and never interprets queue semantics.

The DB IS the work queue (rule #2). There are exactly two claim sources:
  1. claim_pending()  — a PENDING row (brand-new v1, or a retried row). Claimed by
     an atomic status compare-and-set PENDING -> INDEXING; processed IN PLACE.
  2. claim_reindex()  — a served (is_active, INDEXED) row flagged needs_reindex.
     Claimed by an atomic flag compare-and-set needs_reindex True -> False; the row
     STAYS served while the worker reprocesses it, then version+1 supersedes it only
     once the new row is INDEXED (continuous availability — rule #4; mirrors
     knowledge_evolution _supersede).

Commit discipline: the two claim functions COMMIT internally (the claim must be
durable + release the row before slow processing). Every other function FLUSHes
and lets the caller (ContentWorker / ContentIndexer) own the transaction boundary
— the same convention as knowledge_evolution_service.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database.models import ContentIndexState, IndexedContent
from services import content_processor
from services.content_providers import IndexedContentCandidate
from services.content_processor import ProcessedDocument
from utils.logger import get_logger
from utils.time_utils import utcnow

logger = get_logger("services.content_catalog")

# Work-state constants (kept as literals on the rows; named here for clarity).
PENDING = "PENDING"
INDEXING = "INDEXING"
INDEXED = "INDEXED"
ERROR = "ERROR"

_STATE_ROW_ID = 1
_MAX_ERROR_CHARS = 1000


# ── Cache path + source-path confinement ──────────────────────────────────────
def text_cache_path(content_hash: str) -> Path:
    """Absolute path of the extracted-text cache file (single source of truth in
    content_processor)."""
    return content_processor.text_cache_path(content_hash)


def resolve_source_path(row: IndexedContent) -> Path:
    """Re-derive the absolute on-disk path for a LOCAL row from its
    source_metadata {root, relative_path}, CONFINED to that root. Raises on
    traversal/escape or a non-LOCAL source. No filesystem path is ever returned
    to the UI — callers stream bytes by content_id only."""
    if row.source_type != "LOCAL":
        raise NotImplementedError(f"resolve_source_path: unsupported source_type {row.source_type}")
    meta = row.source_metadata or {}
    root_raw = meta.get("root")
    rel_raw = meta.get("relative_path")
    if not root_raw or rel_raw is None:
        raise ValueError(f"row {row.id} has no LOCAL source_metadata root/relative_path")
    root = Path(root_raw).resolve()
    target = (root / rel_raw).resolve()
    if not target.is_relative_to(root):
        raise PermissionError(f"path traversal rejected for row {row.id}: {rel_raw}")
    return target


# ── Generation counter (rule #14) ─────────────────────────────────────────────
def _state_row(db: Session) -> ContentIndexState:
    state = db.get(ContentIndexState, _STATE_ROW_ID)
    if state is None:
        state = ContentIndexState(id=_STATE_ROW_ID, generation=0, updated_at=utcnow())
        db.add(state)
        db.flush()
    return state


def read_generation(db: Session) -> int:
    return int(_state_row(db).generation or 0)


def bump_generation(db: Session) -> int:
    """Increment the single generation counter — called by the worker on any
    mutation of the SERVED set, so each API process's retrieval cache
    (query_hash + generation) self-invalidates. Flushes; caller commits."""
    state = _state_row(db)
    state.generation = int(state.generation or 0) + 1
    state.updated_at = utcnow()
    db.flush()
    return int(state.generation)


# ── Reads ─────────────────────────────────────────────────────────────────────
def get_active(db: Session, source_type: str, source_identifier: str) -> Optional[IndexedContent]:
    return (
        db.query(IndexedContent)
        .filter(
            IndexedContent.source_type == source_type,
            IndexedContent.source_identifier == source_identifier,
            IndexedContent.is_active.is_(True),
        )
        .first()
    )


def list_served(db: Session, limit: Optional[int] = None) -> list[IndexedContent]:
    """The retrieval candidate set: is_active AND index_status='INDEXED'."""
    q = (
        db.query(IndexedContent)
        .filter(IndexedContent.is_active.is_(True), IndexedContent.index_status == INDEXED)
        .order_by(IndexedContent.id)
    )
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def counts_by_status(db: Session) -> dict[str, int]:
    rows = (
        db.query(IndexedContent.index_status, func.count(IndexedContent.id))
        .group_by(IndexedContent.index_status)
        .all()
    )
    return {status: int(count) for status, count in rows}


# ── Change detection (rule #5) ────────────────────────────────────────────────
def needs_reindex(
    row: IndexedContent,
    content_hash: str,
    parser_version: str,
    pipeline_version: str,
    embedding_model: Optional[str],
) -> bool:
    """True when the row's stored content/lifecycle versions differ from current.
    Embedding drift counts only when a NEW model is actually available — so a
    temporary embedder outage (embedding_model=None) never discards an existing
    embedding."""
    if row.content_hash != content_hash:
        return True
    if row.parser_version != parser_version:
        return True
    if row.pipeline_version != pipeline_version:
        return True
    if embedding_model is not None and row.embedding_model != embedding_model:
        return True
    return False


# ── Metadata copy helpers ─────────────────────────────────────────────────────
def _apply_candidate_meta(row: IndexedContent, candidate: IndexedContentCandidate) -> None:
    row.source_type = candidate.source_type
    row.source_identifier = candidate.source_identifier
    row.source_metadata = candidate.source_metadata
    row.root_label = candidate.root_label
    row.filename = candidate.filename
    row.folder = candidate.folder
    row.folder_path = candidate.folder_path
    row.extension = candidate.extension
    row.size_bytes = candidate.size_bytes
    row.modified_at = candidate.modified_at


def _apply_processed(row: IndexedContent, processed: ProcessedDocument) -> None:
    row.content_hash = processed.content_hash
    row.text_cache_path = processed.text_cache_path
    row.keywords = processed.keywords
    row.entities = processed.entities
    row.embedding = processed.embedding
    row.embedding_model = processed.embedding_model
    row.embedding_dimension = processed.embedding_dimension
    row.parser_version = processed.parser_version
    row.pipeline_version = processed.pipeline_version
    row.text_extracted_at = processed.text_extracted_at
    row.embedded_at = processed.embedded_at


# ── Enqueue / reconcile (cheap; no bytes — rule #2) ───────────────────────────
def reconcile_candidate(db: Session, candidate: IndexedContentCandidate) -> str:
    """Reconcile one discovered candidate against the catalog using only cheap
    stat/version signals (no file read). Returns the action taken:
    new | reindex | retry | in_flight | error_capped | unchanged. Flushes;
    caller commits."""
    active = get_active(db, candidate.source_type, candidate.source_identifier)
    now = utcnow()

    if active is None:
        row = IndexedContent(
            index_status=PENDING,
            needs_reindex=False,
            version=1,
            is_active=True,
            valid_from=now,
            created_at=now,
        )
        _apply_candidate_meta(row, candidate)
        db.add(row)
        db.flush()
        return "new"

    if active.index_status == INDEXED:
        changed_stat = (
            active.modified_at != candidate.modified_at
            or active.size_bytes != candidate.size_bytes
        )
        embed_drift = bool(active.embedding_model) and active.embedding_model != content_processor.EMBEDDING_MODEL
        version_drift = (
            active.parser_version != content_processor.PARSER_VERSION
            or active.pipeline_version != content_processor.PIPELINE_VERSION
            or embed_drift
        )
        if (changed_stat or version_drift) and not active.needs_reindex:
            active.needs_reindex = True
            db.flush()
            return "reindex"
        return "unchanged"

    if active.index_status == ERROR:
        changed_stat = (
            active.modified_at != candidate.modified_at
            or active.size_bytes != candidate.size_bytes
        )
        if changed_stat:
            active.index_status = PENDING
            active.retry_count = 0
            active.next_retry_at = None
            active.index_error = None
            _apply_candidate_meta(active, candidate)
            db.flush()
            return "retry"
        return "error_capped"

    # PENDING or INDEXING — already queued / being worked. Refresh lightweight
    # display metadata but do not disturb the queue state.
    _apply_candidate_meta(active, candidate)
    db.flush()
    return "in_flight"


def mark_missing_inactive(db: Session, source_type: str, present_identifiers: set[str]) -> int:
    """After a FULL scan, deactivate active rows of this source whose identifier
    is no longer present (deleted/renamed). Returns the count deactivated. Flushes;
    caller commits (and should bump_generation if >0)."""
    now = utcnow()
    rows = (
        db.query(IndexedContent)
        .filter(IndexedContent.source_type == source_type, IndexedContent.is_active.is_(True))
        .all()
    )
    count = 0
    for row in rows:
        if row.source_identifier not in present_identifiers:
            row.is_active = False
            row.valid_to = now
            row.needs_reindex = False
            count += 1
    if count:
        db.flush()
        logger.info(f"Deactivated {count} missing {source_type} content rows")
    return count


# ── Claim (COMMITS internally to release the row before slow processing) ──────
def claim_pending(db: Session) -> Optional[IndexedContent]:
    """Claim one PENDING row (respecting backoff) via an atomic compare-and-set
    PENDING -> INDEXING. Returns the claimed row (mode "new") or None."""
    now = utcnow()
    row = (
        db.query(IndexedContent)
        .filter(
            IndexedContent.index_status == PENDING,
            or_(IndexedContent.next_retry_at.is_(None), IndexedContent.next_retry_at <= now),
        )
        .order_by(IndexedContent.created_at, IndexedContent.id)
        .first()
    )
    if row is None:
        return None
    updated = (
        db.query(IndexedContent)
        .filter(IndexedContent.id == row.id, IndexedContent.index_status == PENDING)
        .update({IndexedContent.index_status: INDEXING}, synchronize_session=False)
    )
    db.commit()
    if updated != 1:
        return None  # lost the race to another worker — caller polls again
    db.refresh(row)
    return row


def claim_reindex(db: Session) -> Optional[IndexedContent]:
    """Claim one served row flagged needs_reindex via an atomic flag
    compare-and-set True -> False. The row STAYS served while reprocessed.
    Returns the claimed row (mode "reindex") or None."""
    row = (
        db.query(IndexedContent)
        .filter(
            IndexedContent.is_active.is_(True),
            IndexedContent.index_status == INDEXED,
            IndexedContent.needs_reindex.is_(True),
        )
        .order_by(IndexedContent.id)
        .first()
    )
    if row is None:
        return None
    updated = (
        db.query(IndexedContent)
        .filter(IndexedContent.id == row.id, IndexedContent.needs_reindex.is_(True))
        .update({IndexedContent.needs_reindex: False}, synchronize_session=False)
    )
    db.commit()
    if updated != 1:
        return None
    db.refresh(row)
    return row


def reclaim_stale_indexing(db: Session) -> int:
    """On startup, return rows stuck in INDEXING (worker crashed mid-item) to the
    queue (rule #7). Flushes; caller commits."""
    rows = db.query(IndexedContent).filter(IndexedContent.index_status == INDEXING).all()
    for row in rows:
        row.index_status = PENDING
        row.next_retry_at = None
    if rows:
        db.flush()
        logger.info(f"Reclaimed {len(rows)} stale INDEXING rows -> PENDING")
    return len(rows)


# ── Worker write path ─────────────────────────────────────────────────────────
def finalize_new(db: Session, row: IndexedContent, processed: ProcessedDocument,
                 candidate: IndexedContentCandidate) -> None:
    """Complete a brand-new / retried PENDING row IN PLACE → INDEXED (served).
    Flushes; caller commits + bumps generation."""
    now = utcnow()
    _apply_candidate_meta(row, candidate)
    _apply_processed(row, processed)
    row.index_status = INDEXED
    row.indexed_at = now
    row.index_error = None
    row.retry_count = 0
    row.next_retry_at = None
    row.needs_reindex = False
    if row.valid_from is None:
        row.valid_from = now
    db.flush()
    logger.info(f"Indexed new content id={row.id} file={row.filename} hash={row.content_hash[:12]}")


def supersede_with_reindex(db: Session, predecessor: IndexedContent, processed: ProcessedDocument,
                           candidate: IndexedContentCandidate) -> Optional[IndexedContent]:
    """Reindex result for a served row. If content + lifecycle versions are
    unchanged (false-positive flag), refresh stat in place and return None. Else
    write a new INDEXED active version and deactivate the predecessor only after
    the new row is ready (continuous availability — mirrors knowledge _supersede).
    Flushes; caller commits + bumps generation when a new version is returned."""
    now = utcnow()
    if not needs_reindex(
        predecessor,
        processed.content_hash,
        processed.parser_version,
        processed.pipeline_version,
        processed.embedding_model,
    ):
        # False alarm — bytes identical, versions current. Refresh stat so future
        # scans stop re-flagging a merely-touched file. No supersession, no bump.
        _apply_candidate_meta(predecessor, candidate)
        db.flush()
        logger.info(f"Reindex no-op for id={predecessor.id} file={predecessor.filename} (unchanged)")
        return None

    new_row = IndexedContent(
        index_status=INDEXED,
        indexed_at=now,
        needs_reindex=False,
        version=(predecessor.version or 1) + 1,
        is_active=False,            # created inactive so the partial-unique-on-active never collides
        valid_from=now,
    )
    _apply_candidate_meta(new_row, candidate)
    _apply_processed(new_row, processed)
    db.add(new_row)
    db.flush()

    # Deactivate the predecessor, then activate the new row (mirrors _supersede).
    predecessor.is_active = False
    predecessor.valid_to = now
    predecessor.superseded_by_id = new_row.id
    db.flush()

    new_row.is_active = True
    db.flush()
    logger.info(
        f"Superseded content key={new_row.source_identifier} "
        f"old_id={predecessor.id}(v{predecessor.version}) -> new_id={new_row.id}(v{new_row.version})"
    )
    return new_row


def mark_error(db: Session, row: IndexedContent, error: str, max_retries: int,
               backoff_seconds: int) -> None:
    """Record a processing failure with capped retry/backoff (rule #7). Below the
    cap → PENDING with a future next_retry_at; at the cap → terminal ERROR.
    Flushes; caller commits."""
    from datetime import timedelta

    row.retry_count = (row.retry_count or 0) + 1
    row.index_error = (error or "")[:_MAX_ERROR_CHARS]
    if row.retry_count >= max_retries:
        row.index_status = ERROR
        row.next_retry_at = None
        logger.warning(f"Content id={row.id} file={row.filename} ERROR (capped at {max_retries}): {row.index_error}")
    else:
        row.index_status = PENDING
        row.next_retry_at = utcnow() + timedelta(seconds=backoff_seconds * row.retry_count)
        logger.warning(
            f"Content id={row.id} file={row.filename} failed "
            f"(retry {row.retry_count}/{max_retries}, next {row.next_retry_at}): {row.index_error}"
        )
    db.flush()
