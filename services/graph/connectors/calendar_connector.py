"""
CalendarConnector — Outlook calendar event sync via Microsoft Graph delta queries.

Endpoint: GET /users/{upn}/calendarView/delta or /events/delta
Delta tokens stored in graph_sync_state (source_type='calendar').
"""

from typing import Optional

from utils.config import settings
from utils.logger import get_logger
from services.graph.token_manager import graph_get

logger = get_logger("services.graph.connectors.calendar")

_SELECT = (
    "id,subject,start,end,organizer,attendees,body,importance,"
    "isOnlineMeeting,onlineMeetingUrl,recurrence,isCancelled,isAllDay,hasAttachments"
)


class CalendarConnector:

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def fetch_delta(self, delta_token: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
        """
        Fetch new/changed calendar events since delta_token.
        Returns (items, new_delta_token).
        """
        if not self.upn:
            raise RuntimeError("GRAPH_SERVICE_UPN not configured.")

        # NOTE: /events/delta (change tracking) rejects $top with 400
        # ErrorInvalidUrlQuery — page size must be expressed via the
        # odata.maxpagesize Prefer header, not $top. We rely on Graph's default
        # page size and follow @odata.nextLink for pagination.
        path = f"users/{self.upn}/events/delta"
        params: dict = {"$select": _SELECT}

        if delta_token:
            params["$deltatoken"] = delta_token
        else:
            logger.info(f"CalendarConnector: full sync for {self.upn}")

        items: list[dict] = []
        new_delta_token: Optional[str] = None

        while True:
            data = graph_get(path, params)
            page_items = data.get("value", [])
            items.extend([i for i in page_items if not i.get("@removed")])

            next_link = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")

            if delta_link:
                new_delta_token = (
                    delta_link.split("$deltatoken=", 1)[-1]
                    if "$deltatoken=" in delta_link
                    else delta_link
                )
                break

            if next_link:
                path = next_link.replace("https://graph.microsoft.com/v1.0/", "")
                params = {}
            else:
                break

        # Enrich events that have attachments with attachment metadata so the
        # (deterministic, no-AI) meeting-prep document retrieval can surface them.
        # This is background ingest work — never on the meeting-time path.
        for ev in items:
            if ev.get("hasAttachments"):
                ev["attachments"] = self._fetch_attachments(ev.get("id", ""))

        logger.info(
            f"CalendarConnector: fetched {len(items)} events for {self.upn}"
        )
        return items, new_delta_token

    def _fetch_attachments(self, event_id: str) -> list[dict]:
        """Return [{id, name, contentType, size, url}] for an event. Best-effort, fail-soft."""
        if not event_id:
            return []
        try:
            data = graph_get(
                f"users/{self.upn}/events/{event_id}/attachments",
                {"$select": "id,name,contentType,size"},
            )
            return [
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "contentType": a.get("contentType"),
                    "size": a.get("size"),
                    "url": (
                        f"https://graph.microsoft.com/v1.0/users/{self.upn}/events/"
                        f"{event_id}/attachments/{a['id']}/$value"
                        if a.get("id") else None
                    ),
                }
                for a in data.get("value", [])
                if a.get("name")
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Attachment fetch failed for event {event_id}: {e}")
            return []
