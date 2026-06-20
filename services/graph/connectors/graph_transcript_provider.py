"""
GraphTranscriptProvider — live Microsoft Graph implementation of the
TranscriptProvider Protocol (Plan 3 §11). Swaps in for MockTranscriptProvider
without touching anything downstream: it produces the exact same Transcript
domain model via the shared parse_transcript_payload().

Meeting discovery reuses already-synced CalendarEvent rows (is_online_meeting,
meeting_url) rather than a tenant-wide onlineMeetings listing — avoiding new
Graph permission/listing-limitation risk (CalendarConnector already syncs these).

Fetch path per candidate meeting (all read-only, via the existing token_manager):
  1. resolve the onlineMeeting from its join URL:
       GET /users/{upn}/onlineMeetings?$filter=JoinWebUrl eq '{join_url}'
     (the returned resource IS the parser's meeting_metadata)
  2. list transcripts:
       GET /users/{upn}/onlineMeetings/{meetingId}/transcripts
  3. fetch VTT content:
       GET /users/{upn}/onlineMeetings/{meetingId}/transcripts/{id}/content?$format=text/vtt

NOTE: the onlineMeetings call shapes should be validated against the real tenant
during rollout (Plan 3 flagged this) — the parser and everything past it are
already proven by MockTranscriptProvider's identical fixtures.
"""

from typing import Optional

from utils.config import settings
from utils.logger import get_logger
from database.connection import SessionLocal
from database.models import CalendarEvent
from services.graph.transcript_models import Transcript
from services.graph.transcript_payload_parser import parse_transcript_payload
from services.graph.token_manager import graph_get, graph_get_stream

logger = get_logger("services.graph.connectors.graph_transcript")


class GraphTranscriptProvider:
    """Live Graph provider — same fetch_since() contract as MockTranscriptProvider."""

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def fetch_since(self, since_iso: Optional[str] = None) -> tuple[list[Transcript], str]:
        if not self.upn:
            raise RuntimeError("GRAPH_SERVICE_UPN not configured.")

        cursor = since_iso or "2020-01-01T00:00:00Z"
        latest_ts = cursor
        transcripts: list[Transcript] = []

        for join_url in self._candidate_join_urls():
            online_meeting = self._resolve_online_meeting(join_url)
            if not online_meeting:
                continue
            meeting_id = online_meeting.get("id")
            if not meeting_id:
                continue

            for tmeta in self._list_transcripts(meeting_id):
                created = tmeta.get("createdDateTime", "")
                if since_iso and created and created <= since_iso:
                    continue
                vtt = self._fetch_vtt(meeting_id, tmeta.get("id"))
                if vtt is None:
                    continue
                transcripts.append(parse_transcript_payload(tmeta, vtt, online_meeting))
                if created and created > latest_ts:
                    latest_ts = created

        logger.info(f"GraphTranscriptProvider: fetched {len(transcripts)} transcript(s) since {cursor}")
        return transcripts, latest_ts

    # ── discovery (reuses already-synced calendar data) ──────────────────────
    def _candidate_join_urls(self) -> list[str]:
        db = SessionLocal()
        try:
            rows = (
                db.query(CalendarEvent.meeting_url)
                .filter(
                    CalendarEvent.is_online_meeting.is_(True),
                    CalendarEvent.is_cancelled.is_(False),
                    CalendarEvent.meeting_url.isnot(None),
                )
                .all()
            )
            # de-dup while preserving order
            seen, urls = set(), []
            for (url,) in rows:
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
            return urls
        finally:
            db.close()

    # ── Graph reads (each guarded; a single bad meeting never aborts the run) ─
    def _resolve_online_meeting(self, join_url: str) -> Optional[dict]:
        try:
            data = graph_get(
                f"users/{self.upn}/onlineMeetings",
                {"$filter": f"JoinWebUrl eq '{join_url}'"},
            )
        except Exception as e:
            logger.warning(f"GraphTranscriptProvider: onlineMeeting resolve failed — {e}")
            return None
        items = data.get("value", [])
        return items[0] if items else None

    def _list_transcripts(self, meeting_id: str) -> list[dict]:
        try:
            data = graph_get(f"users/{self.upn}/onlineMeetings/{meeting_id}/transcripts")
        except Exception as e:
            logger.warning(f"GraphTranscriptProvider: transcript list failed for {meeting_id} — {e}")
            return []
        return data.get("value", [])

    def _fetch_vtt(self, meeting_id: str, transcript_id: Optional[str]) -> Optional[str]:
        if not transcript_id:
            return None
        try:
            resp = graph_get_stream(
                f"users/{self.upn}/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content",
                {"$format": "text/vtt"},
            )
            return resp.text
        except Exception as e:
            logger.warning(f"GraphTranscriptProvider: VTT fetch failed for {transcript_id} — {e}")
            return None
