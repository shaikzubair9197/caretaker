"""
CalendarWriter — create an Outlook calendar event via Microsoft Graph (write side).

Endpoint: POST /users/{upn}/events

Pure Graph I/O — no DB, no vault, no masking — mirroring the read-side
CalendarConnector. The executor decrypts attendee addresses and hands plaintext
here only for the duration of the call; attendee addresses are never logged.
"""

from datetime import datetime, timedelta
from typing import Optional

from utils.config import settings
from utils.logger import get_logger
from services.graph.token_manager import graph_post
from services.graph.senders.errors import classify_graph_error

logger = get_logger("services.graph.senders.calendar")


def _normalize(dt_iso: str) -> str:
    """Graph wants a naive 'YYYY-MM-DDTHH:MM:SS' string paired with a timeZone
    field. Accept ISO input with or without a trailing 'Z'/offset and emit that
    shape."""
    parsed = datetime.fromisoformat(dt_iso.replace("Z", "+00:00"))
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


class CalendarWriter:

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def create_event(
        self,
        subject: str,
        start_iso: str,
        duration_minutes: int,
        attendee_emails: Optional[list[str]] = None,
    ) -> dict:
        """
        POST /users/{upn}/events. Returns {"event_id": <id>}.
        Times are treated as UTC (timeZone field carries 'UTC').
        """
        if not self.upn:
            raise RuntimeError("GRAPH_SERVICE_UPN not configured.")

        start_fmt = _normalize(start_iso)
        end_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")) + timedelta(
            minutes=int(duration_minutes or 30)
        )
        end_fmt = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

        body = {
            "subject": subject,
            "start": {"dateTime": start_fmt, "timeZone": "UTC"},
            "end": {"dateTime": end_fmt, "timeZone": "UTC"},
            "attendees": [
                {"emailAddress": {"address": e}, "type": "required"}
                for e in (attendee_emails or [])
            ],
        }
        try:
            data = graph_post(f"users/{self.upn}/events", json_body=body)
        except Exception as exc:
            raise classify_graph_error(exc) from exc

        event_id = data.get("id")
        logger.info(f"CalendarWriter: created event id={event_id} via {self.upn}")
        return {"event_id": event_id}
