"""
ChatConnector — Teams chat message sync via Microsoft Graph.

Endpoint: GET /users/{upn}/chats → GET /chats/{id}/messages
No native delta support for chat messages — uses createdDateTime cursor stored
in GraphSyncState as delta_token (ISO timestamp string).
"""

from datetime import datetime, timezone
from typing import Optional

from utils.config import settings
from utils.logger import get_logger
from services.graph.token_manager import graph_get

logger = get_logger("services.graph.connectors.chat")

_TOP = 50
_MSG_SELECT = "id,chatId,from,body,createdDateTime,messageType,importance,attachments,replyToId,mentions"


class ChatConnector:

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def _list_chat_ids(self) -> list[str]:
        """Return IDs of all chats the service account participates in."""
        if not self.upn:
            raise RuntimeError("GRAPH_SERVICE_UPN not configured.")
        data = graph_get(f"users/{self.upn}/chats", {"$top": 50, "$select": "id"})
        return [c["id"] for c in data.get("value", []) if c.get("id")]

    def fetch_since(self, since_iso: Optional[str] = None) -> tuple[list[dict], str]:
        """
        Fetch all chat messages created after since_iso.
        Returns (items, new_cursor_iso).
        new_cursor_iso is the ISO timestamp of the latest message seen.
        """
        chat_ids = self._list_chat_ids()
        items: list[dict] = []
        latest_ts = since_iso or "2020-01-01T00:00:00Z"

        for chat_id in chat_ids:
            path = f"chats/{chat_id}/messages"
            params: dict = {"$top": _TOP, "$select": _MSG_SELECT}
            if since_iso:
                params["$filter"] = f"createdDateTime ge {since_iso}"

            while True:
                try:
                    data = graph_get(path, params)
                except Exception as e:
                    logger.warning(f"ChatConnector: skipping chat {chat_id[:12]}… — {e}")
                    break

                for msg in data.get("value", []):
                    if msg.get("messageType") != "message":
                        continue
                    msg["chatId"] = chat_id
                    items.append(msg)
                    ts = msg.get("createdDateTime", "")
                    if ts > latest_ts:
                        latest_ts = ts

                next_link = data.get("@odata.nextLink")
                if next_link:
                    path = next_link.replace("https://graph.microsoft.com/v1.0/", "")
                    params = {}
                else:
                    break

        logger.info(
            f"ChatConnector: fetched {len(items)} messages "
            f"across {len(chat_ids)} chats for {self.upn}"
        )
        return items, latest_ts
