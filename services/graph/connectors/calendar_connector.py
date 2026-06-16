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

_TOP = 20
_SELECT = (
    "id,subject,start,end,organizer,attendees,body,importance,"
    "isOnlineMeeting,onlineMeetingUrl,recurrence,isCancelled,isAllDay"
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

        path = f"users/{self.upn}/events/delta"
        params: dict = {"$top": _TOP, "$select": _SELECT}

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

        logger.info(
            f"CalendarConnector: fetched {len(items)} events for {self.upn}"
        )
        return items, new_delta_token
