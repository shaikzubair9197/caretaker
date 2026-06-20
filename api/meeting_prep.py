"""
Meeting Prep API — deterministic, no-AI retrieval for the meeting-prep popup.

Endpoints:
  GET  /meeting/prep/next      — soonest meeting needing prep (full snapshot)
  GET  /meeting/prep/upcoming  — lightweight list for the scheduler poll
  GET  /meeting/prep/{event_id}— full snapshot for a specific event
  POST /meeting/prep/sync      — force a live calendar sync (thin reuse of graph_sync)

All snapshots are built from the plaintext source archive — no masking, no LLM.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from services.graph.token_manager import graph_get_stream
from services.meeting_prep_service import MeetingPrepService
from utils.config import settings
from utils.logger import get_logger
from utils.time_utils import utcnow

router = APIRouter(prefix="/meeting/prep", tags=["meeting-prep"])
logger = get_logger("api.meeting_prep")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/next")
def prep_next(
    within_minutes: int = Query(default=15, ge=1, le=240),
    force: bool = Query(default=False, description="Ignore the window; soonest meeting (demo)."),
    db: Session = Depends(get_db),
):
    return {"event": MeetingPrepService.next_snapshot(db, within_minutes, force)}


@router.get("/upcoming")
def prep_upcoming(
    within_minutes: int = Query(default=15, ge=1, le=240),
    db: Session = Depends(get_db),
):
    now = utcnow()
    events = MeetingPrepService.get_upcoming(db, within_minutes, force=False)
    return [
        {
            "external_id": e.external_id,
            "start": e.start_at.isoformat() if e.start_at else None,
            "minutes_until": max(0, int((e.start_at - now).total_seconds() // 60)) if e.start_at else None,
        }
        for e in events
    ]


@router.get("/recently-completed")
def prep_recently_completed(
    within_minutes: int = Query(default=30, ge=1, le=240),
    db: Session = Depends(get_db),
):
    """Online meetings that ended within the window and have no transcript yet — drives the scheduler's post-meeting poll."""
    events = MeetingPrepService.get_recently_completed_needing_transcript(db, within_minutes)
    return [
        {
            "external_id": e.external_id,
            "end": e.end_at.isoformat() if e.end_at else None,
        }
        for e in events
    ]


@router.get("/attachment/{event_id}/{attachment_id}")
def prep_attachment(event_id: str, attachment_id: str):
    if not settings.GRAPH_SERVICE_UPN:
        raise HTTPException(503, "GRAPH_SERVICE_UPN not configured.")

    path = f"users/{settings.GRAPH_SERVICE_UPN}/events/{event_id}/attachments/{attachment_id}/$value"
    resp = graph_get_stream(path)

    headers = {}
    if resp.headers.get("content-type"):
        headers["content-type"] = resp.headers.get("content-type")
    if resp.headers.get("content-disposition"):
        headers["content-disposition"] = resp.headers.get("content-disposition")

    return StreamingResponse(resp.iter_bytes(), status_code=resp.status_code, headers=headers)


@router.get("/{event_id}")
def prep_for_event(event_id: str, db: Session = Depends(get_db)):
    snap = MeetingPrepService.snapshot_for(db, event_id)
    return {"event": snap}


@router.post("/sync")
def prep_sync(
    upn: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Force a live calendar sync so the prep DB is fresh. Thin reuse of graph_sync."""
    # Imported lazily so the API module loads even if Graph deps are unavailable.
    from api.graph_sync import _run_source
    from database.models import GraphSyncState
    from services.vault_service import VaultService

    target_upn = upn or settings.GRAPH_SERVICE_UPN
    if not target_upn:
        raise HTTPException(400, "UPN not specified and GRAPH_SERVICE_UPN not configured.")
    if not VaultService.is_configured():
        raise HTTPException(
            503,
            "VAULT_MASTER_KEY not configured — calendar ingest cannot store tokens. "
            "Add VAULT_MASTER_KEY=<64-hex-chars> to .env and restart.",
        )

    # Always full-sync the calendar: it is small, and prep must reflect the current
    # calendar state. Calendar delta can miss freshly-created events, so we clear the
    # delta token to force a full re-read each time.
    state = db.query(GraphSyncState).filter_by(
        source_type="calendar", user_upn=target_upn
    ).first()
    if state and state.delta_token:
        state.delta_token = None
        db.commit()

    result = _run_source("calendar", target_upn, db, dry_run=False)
    return result
