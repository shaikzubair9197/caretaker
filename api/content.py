"""
Content API — preview, raw stream, reveal and index status for the Content
Retrieval catalog (Document Retrieval plan — Phase 2).

Documents are addressed by content_id ONLY — no filesystem path is ever accepted
from or returned to the client. Raw bytes are streamed from a path-confined
resolution (services.content_catalog.resolve_source_path); "reveal" opens the
containing folder server-side (the API runs on the same host as the desktop UI).

Mounted under the global X-API-Key auth in app.py.
"""

import subprocess
import sys
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from services import content_catalog, content_preview, content_summary
from utils.logger import get_logger

router = APIRouter(prefix="/content", tags=["content"])
logger = get_logger("api.content")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/index/status")
def index_status(db: Session = Depends(get_db)):
    """Catalog health: per-status counts, the served total and the generation."""
    return {
        "counts": content_catalog.counts_by_status(db),
        "served": len(content_catalog.list_served(db)),
        "generation": content_catalog.read_generation(db),
    }


@router.get("/{content_id}/preview")
def preview(
    content_id: int,
    q: Optional[str] = Query(default=None, description="Optional query to centre the snippet on."),
    db: Session = Depends(get_db),
):
    result = content_preview.snippet(db, content_id, q)
    if result is None:
        raise HTTPException(404, "Content not found or not indexed.")
    return result


@router.get("/{content_id}/summary")
def summary(
    content_id: int,
    refresh: bool = Query(default=False, description="Force regeneration, ignoring the cached summary."),
    db: Session = Depends(get_db),
):
    """Masked LLM summary of a document. The text is PII/credential-masked before
    the LLM is called — real secrets never reach the model. The result is cached
    by content hash (no re-call / token waste on repeat views); pass refresh=true
    to regenerate. Returns 404 if the content isn't served; otherwise a structured
    result (ok / empty / llm_error)."""
    result = content_summary.summarize(db, content_id, refresh=refresh)
    if result.get("status") == "not_found":
        raise HTTPException(404, "Content not found or not indexed.")
    return result


@router.get("/{content_id}/raw")
def raw(content_id: int, db: Session = Depends(get_db)):
    """Stream the raw document bytes (path-confined). The server path is never
    exposed — only the bytes and the display filename."""
    resolved = content_preview.resolve(db, content_id)
    if resolved is None:
        raise HTTPException(404, "Content not found.")
    path, filename, media_type = resolved
    if not path.exists():
        raise HTTPException(404, "Source file is no longer available.")
    return FileResponse(str(path), filename=filename, media_type=media_type)


def _open_in_file_manager(path) -> bool:
    """Best-effort server-side folder reveal. Factored out so tests can patch it."""
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(path)])
        else:
            return False
        return True
    except Exception as e:  # noqa: BLE001 - reveal is best-effort
        logger.warning(f"Reveal failed: {e}")
        return False


@router.post("/{content_id}/reveal")
def reveal(content_id: int, db: Session = Depends(get_db)):
    """Open the document's containing folder server-side. Returns only the folder
    name (the absolute path stays on the server)."""
    info = content_preview.folder_for_reveal(db, content_id)
    if info is None:
        raise HTTPException(404, "Content not found.")
    revealed = _open_in_file_manager(info["path"])
    return {"revealed": revealed, "folder": info["folder"]}
