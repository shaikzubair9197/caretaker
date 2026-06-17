"""
EmailConnector — incremental Outlook email sync via Microsoft Graph delta queries.

Endpoint: GET /users/{upn}/mailFolders/{folder}/messages/delta
Message change tracking is NOT supported on the unscoped /messages/delta path
("Change tracking is not supported against microsoft.graph.message") — it must be
scoped to a mail folder. We sync the inbox by default.
Delta tokens stored in graph_sync_state (source_type='outlook_email').
"""

import os
from typing import Optional

from utils.config import settings
from utils.logger import get_logger
from services.graph.token_manager import graph_get

logger = get_logger("services.graph.connectors.email")

_SELECT = (
    "id,subject,from,toRecipients,ccRecipients,body,receivedDateTime,"
    "hasAttachments,importance,conversationId,internetMessageId,parentFolderId"
)
_FOLDER = os.getenv("EMAIL_SYNC_FOLDER", "inbox")


class EmailConnector:

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def fetch_delta(self, delta_token: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
        """
        Fetch new/changed emails since delta_token.
        Returns (items, new_delta_token).
        new_delta_token should be persisted to graph_sync_state immediately.
        """
        if not self.upn:
            raise RuntimeError("GRAPH_SERVICE_UPN not configured.")

        # Message delta must be scoped to a mail folder (unscoped /messages/delta
        # returns 400 "Change tracking is not supported against ...message").
        # Delta endpoints also reject $top — page size is set via the
        # odata.maxpagesize Prefer header; we use Graph's default + @odata.nextLink.
        path = f"users/{self.upn}/mailFolders/{_FOLDER}/messages/delta"
        params: dict = {"$select": _SELECT}

        if delta_token:
            # delta_token is the full @odata.deltaLink URL — use as-is
            # Strip the base URL to extract just the token value for the delta param
            if "@odata.deltaLink" in delta_token:
                delta_token = delta_token.split("$deltatoken=", 1)[-1]
            params["$deltatoken"] = delta_token
        else:
            logger.info(f"EmailConnector: full sync for {self.upn}")

        items: list[dict] = []
        new_delta_token: Optional[str] = None

        while True:
            data = graph_get(path, params)
            page_items = data.get("value", [])
            items.extend([i for i in page_items if not i.get("@removed")])

            next_link = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")

            if delta_link:
                # Extract just the token value
                new_delta_token = delta_link.split("$deltatoken=", 1)[-1] if "$deltatoken=" in delta_link else delta_link
                break

            if next_link:
                # Parse path from next_link
                path = next_link.replace("https://graph.microsoft.com/v1.0/", "")
                params = {}
            else:
                break

        logger.info(
            f"EmailConnector: fetched {len(items)} emails for {self.upn} "
            f"delta_token={'<new>' if new_delta_token else 'none'}"
        )
        return items, new_delta_token
