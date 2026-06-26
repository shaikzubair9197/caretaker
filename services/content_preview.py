"""
content_preview — snippet + raw-byte resolution for served content
(Document Retrieval plan — Phase 2).

`snippet()` reads the on-disk extracted-text cache (never the DB) and returns a
short, query-centred excerpt. `resolve()` returns the path-confined absolute
source path for streaming raw bytes by content_id (the path is used server-side
only — it is never returned to the UI). Both operate strictly on the SERVED set
(is_active AND index_status='INDEXED').
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from database.models import IndexedContent
from services import content_catalog
from utils.config import settings
from utils.logger import get_logger

logger = get_logger("services.content_preview")


def _load_served(db: Session, content_id: int) -> Optional[IndexedContent]:
    row = db.get(IndexedContent, content_id)
    if row is None or not row.is_active or row.index_status != "INDEXED":
        return None
    return row


def snippet(db: Session, content_id: int, query_text: Optional[str] = None,
            max_chars: Optional[int] = None) -> Optional[dict]:
    """Return a preview dict for a served content row, or None if not found/served.
    The snippet is read from the cache file; no filesystem path is exposed."""
    row = _load_served(db, content_id)
    if row is None:
        return None

    limit = max_chars or settings.CONTENT_SNIPPET_CHARS
    text = ""
    if row.content_hash:
        path = content_catalog.text_cache_path(row.content_hash)
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.warning(f"Failed to read text cache for content {content_id}: {e}")

    snip = _best_snippet(text, query_text, limit)
    return {
        "content_id": row.id,
        "filename": row.filename,
        "folder": row.folder,
        "extension": row.extension,
        "snippet": snip,
        "truncated": len(text.strip()) > len(snip),
    }


def _best_snippet(text: str, query_text: Optional[str], max_chars: int) -> str:
    text = (text or "").strip()
    if not text or len(text) <= max_chars:
        return text
    if query_text:
        terms = re.findall(r"[a-z0-9]{3,}", query_text.lower())
        lowered = text.lower()
        for term in terms:
            idx = lowered.find(term)
            if idx != -1:
                start = max(0, idx - max_chars // 3)
                end = min(len(text), start + max_chars)
                prefix = "…" if start > 0 else ""
                suffix = "…" if end < len(text) else ""
                return prefix + text[start:end].strip() + suffix
    return text[:max_chars].strip() + "…"


def resolve(db: Session, content_id: int) -> Optional[tuple[Path, str, str]]:
    """Return (absolute_path, filename, media_type) for a served row, path-confined.
    The path is for server-side streaming only — callers must not expose it."""
    row = _load_served(db, content_id)
    if row is None:
        return None
    try:
        path = content_catalog.resolve_source_path(row)
    except (PermissionError, ValueError, NotImplementedError) as e:
        logger.warning(f"resolve failed for content {content_id}: {e}")
        return None
    media_type = mimetypes.guess_type(row.filename or path.name)[0] or "application/octet-stream"
    return path, (row.filename or path.name), media_type


def folder_for_reveal(db: Session, content_id: int) -> Optional[dict]:
    """For the popup's 'Open Folder': returns the path-confined containing folder
    (server-side path + display name). The absolute path stays server-side; only
    the folder name is suitable for returning to the UI."""
    resolved = resolve(db, content_id)
    if resolved is None:
        return None
    path, _, _ = resolved
    return {"path": path.parent, "folder": path.parent.name}
