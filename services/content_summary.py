"""
content_summary — masked LLM summarization of a served document (Document
Retrieval plan, follow-on).

Security model (mirrors the rest of the project): the document's cached plain
text is MASKED with the existing Presidio pipeline (PreprocessingService.mask_pii)
BEFORE the LLM is ever called. Credentials / API keys / PII are replaced by
[REDACTED:*] tokens, so real secrets never leave the box into the LLM. The LLM
call itself (LLMService.summarize_document) additionally input-guards and audits
the request. Always returns a structured dict (never raises) so the UI can render
a graceful state for every outcome.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from database.models import ContentSummary, IndexedContent
from services import content_catalog
from services.llm_service import LLMService, AZURE_DEPLOYMENT
from services.preprocessing_service import PreprocessingService
from utils.config import settings
from utils.logger import get_logger
from utils.time_utils import utcnow

logger = get_logger("services.content_summary")

# Bump when the summary prompt/format changes — stored summaries with an older
# version are regenerated once on next view (the rest are reused as-is).
SUMMARY_PROMPT_VERSION = "1"


def _get_stored(db: Session, content_hash: str) -> Optional[ContentSummary]:
    """Return the permanent summary for this content, or None if absent or stale
    (prompt version changed). Resilient: any read error (e.g. table not yet
    migrated) falls through to regeneration rather than failing the request."""
    try:
        row = db.query(ContentSummary).filter(ContentSummary.content_hash == content_hash).first()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning(f"summary store read failed for {content_hash[:12]}: {e}")
        return None
    if row is None:
        return None
    if (row.prompt_version or "") != SUMMARY_PROMPT_VERSION:
        return None
    return row


def _store(db: Session, content_hash: str, summary: str, redaction_count: int,
           token_count: int, truncated: bool) -> None:
    """Upsert the permanent summary keyed by content_hash. Best-effort — a store
    failure never blocks returning the freshly-generated summary."""
    try:
        row = db.query(ContentSummary).filter(ContentSummary.content_hash == content_hash).first()
        if row is None:
            row = ContentSummary(content_hash=content_hash)
            db.add(row)
        row.summary = summary
        row.redaction_count = redaction_count
        row.token_count = token_count
        row.truncated = truncated
        row.summary_model = AZURE_DEPLOYMENT or None
        row.prompt_version = SUMMARY_PROMPT_VERSION
        row.created_at = utcnow()
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning(f"summary store failed for {content_hash[:12]}: {e}")


def _load_served(db: Session, content_id: int) -> Optional[IndexedContent]:
    row = db.get(IndexedContent, content_id)
    if row is None or not row.is_active or row.index_status != "INDEXED":
        return None
    return row


def _unmask(text: str, token_map: dict[str, str]) -> str:
    """Restore real values into the summary by replacing each <TOKEN_N> placeholder
    with its original value. Longest token names first so <PERSON_10> is replaced
    before <PERSON_1>."""
    if not text or not token_map:
        return text
    for token_name, original in sorted(token_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        text = text.replace(f"<{token_name}>", original)
    return text


def _audit_unmask(content_id: int, token_count: int, redaction_count: int) -> None:
    """Audit that masked values were restored into a summary shown to the user
    (the security model audits every unmask). Records COUNTS ONLY — never the
    tokens or the secret/PII values. Independent session so it is durable
    regardless of the caller's transaction."""
    from database.connection import SessionLocal
    from database.models import AuditEvent

    session = SessionLocal()
    try:
        session.add(AuditEvent(
            event_type="UNMASK",
            actor="user",
            resource_type="IndexedContent",
            resource_id=content_id,
            outcome="SUCCESS",
            event_data={
                "context": "document_summary",
                "token_count": token_count,
                "redaction_count": redaction_count,
            },
        ))
        session.commit()
    except Exception as e:  # noqa: BLE001 - audit is best-effort, never blocks the summary
        session.rollback()
        logger.warning(f"summary unmask audit write failed: {e}")
    finally:
        session.close()


def summarize(db: Session, content_id: int, refresh: bool = False) -> dict:
    """Summarize a served document via the masked LLM path. Returns one of:
    {status: ok|not_found|empty|llm_error, ...}. The summary is generated from
    MASKED text only, then unmasked for the user.

    The result is stored PERMANENTLY in the DB keyed by content_hash, so the same
    document content reuses the same summary across meetings and restarts and is
    never re-billed. It self-invalidates when the document changes (new hash) or
    the summary prompt version changes. Pass refresh=True to force regeneration."""
    row = _load_served(db, content_id)
    if row is None:
        return {"status": "not_found", "content_id": content_id}

    # ── Permanent store hit: serve the stored summary, no LLM call ────────────
    if row.content_hash and not refresh:
        stored = _get_stored(db, row.content_hash)
        if stored is not None:
            if stored.token_count:
                _audit_unmask(content_id, stored.token_count, stored.redaction_count or 0)
            logger.info(f"summary store hit content_id={content_id} hash={row.content_hash[:12]}")
            return {
                "status": "ok",
                "content_id": content_id,
                "filename": row.filename,
                "summary": stored.summary,
                "masked_from_llm": True,
                "redaction_count": stored.redaction_count or 0,
                "truncated": bool(stored.truncated),
                "cached": True,
            }

    text = ""
    if row.content_hash:
        path = content_catalog.text_cache_path(row.content_hash)
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.warning(f"summary: failed to read cache for content {content_id}: {e}")

    text = (text or "").strip()
    if not text:
        return {"status": "empty", "content_id": content_id, "filename": row.filename}

    head = text[: settings.CONTENT_SUMMARY_MAX_CHARS]
    # ── Mask BEFORE the LLM sees anything, with REVERSIBLE indexed tokens
    #    (<PERSON_1>, <API_KEY_1>, …) so the summary can be restored for the user
    #    afterwards. The cloud LLM only ever sees the masked text. ──────────────
    masked, token_map, redactions = PreprocessingService.mask_pii_indexed(head)

    result = LLMService.summarize_document(masked)
    if result.succeeded:
        raw_summary = ((result.data or {}).get("summary") or "").strip()
        # Unmask for display: the user owns these local documents and is authorised
        # to see the real values — only the cloud LLM was kept blind to them.
        summary = _unmask(raw_summary, token_map)
        if token_map:
            _audit_unmask(content_id, len(token_map), len(redactions))
        truncated = len(text) > len(head)
        if row.content_hash:
            _store(db, row.content_hash, summary, len(redactions), len(token_map), truncated)
        logger.info(
            f"summary generated content_id={content_id} redactions={len(redactions)} "
            f"unmasked_tokens={len(token_map)} truncated={truncated}"
        )
        return {
            "status": "ok",
            "content_id": content_id,
            "filename": row.filename,
            "summary": summary,
            "masked_from_llm": True,
            "redaction_count": len(redactions),
            "truncated": truncated,
            "cached": False,
        }

    logger.warning(f"summary llm failed content_id={content_id} status={result.status}")
    return {
        "status": "llm_error",
        "content_id": content_id,
        "filename": row.filename,
        "reason": result.reason,
        "llm_status": result.status,
    }
