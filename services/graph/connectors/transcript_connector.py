"""
TranscriptConnector — fetch-only access to meeting transcripts.

Mirrors the ChatConnector pattern (no native Graph delta support for
transcripts either — uses a createdDateTime cursor, same as chat).

Architecture:
    TranscriptProvider (Mock now / Graph later)
        -> each provider fetches a raw Graph-shaped payload, then calls the
           shared transcript_payload_parser
        -> returns Transcript domain model (identical regardless of provider)
    TranscriptConnector
        -> thin selector only. No database access, no masking, no LLM calls.
           TranscriptIngestionService owns everything past this point.
"""

from typing import Optional, Protocol

from utils.config import settings
from utils.logger import get_logger
from services.graph.transcript_models import Transcript
from services.graph.transcript_payload_parser import parse_transcript_payload
from services.graph.connectors.mock_transcript_fixtures import ALL_FIXTURES

logger = get_logger("services.graph.connectors.transcript")


class TranscriptProvider(Protocol):
    """Contract every transcript provider (Mock, Graph, ...) must satisfy."""

    def fetch_since(self, since_iso: Optional[str] = None) -> tuple[list[Transcript], str]:
        """Return (transcripts, new_cursor_iso) for all transcripts created after since_iso."""
        ...


class MockTranscriptProvider:
    """
    Production-grade simulation of the Microsoft Graph transcript API.

    Generates raw payloads shaped exactly like the three real Graph resources
    (transcript metadata, WebVTT content, onlineMeeting/participant metadata)
    and runs them through transcript_payload_parser — the same function a
    future GraphTranscriptProvider will use with live HTTP responses. Nothing
    about this class is visible past TranscriptConnector.
    """

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def fetch_since(self, since_iso: Optional[str] = None) -> tuple[list[Transcript], str]:
        cursor = since_iso or "2020-01-01T00:00:00Z"
        latest_ts = cursor
        transcripts: list[Transcript] = []

        for fixture in ALL_FIXTURES:
            created = fixture["transcript_metadata"]["createdDateTime"]
            if since_iso and created <= since_iso:
                continue
            transcripts.append(
                parse_transcript_payload(
                    transcript_metadata=fixture["transcript_metadata"],
                    vtt_content=fixture["vtt_content"],
                    meeting_metadata=fixture["meeting_metadata"],
                )
            )
            if created > latest_ts:
                latest_ts = created

        logger.info(f"MockTranscriptProvider: fetched {len(transcripts)} transcripts since {cursor}")
        return transcripts, latest_ts


class TranscriptConnector:
    """Selects the active TranscriptProvider implementation and delegates to it."""

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN
        self._provider: TranscriptProvider = self._build_provider()

    def _build_provider(self) -> TranscriptProvider:
        provider_name = (settings.TRANSCRIPT_PROVIDER or "mock").lower()
        if provider_name == "mock":
            return MockTranscriptProvider(self.upn)
        if provider_name == "graph":
            # Lazy import: only pull in DB/Graph wiring when actually selected.
            from services.graph.connectors.graph_transcript_provider import GraphTranscriptProvider
            return GraphTranscriptProvider(self.upn)
        raise ValueError(f"Unknown TRANSCRIPT_PROVIDER: {provider_name!r}")

    def fetch_since(self, since_iso: Optional[str] = None) -> tuple[list[Transcript], str]:
        return self._provider.fetch_since(since_iso)
