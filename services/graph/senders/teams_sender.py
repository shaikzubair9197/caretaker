"""
TeamsSender — send a Teams chat message via Microsoft Graph (write side).

Endpoints:
  POST /chats                       — create-or-reuse a one-on-one chat
  POST /chats/{chat-id}/messages    — send the message

Pure Graph I/O — no DB, no vault, no masking — mirroring the read-side
ChatConnector's separation of concerns. The executor decrypts the recipient
identity and hands plaintext here only for the duration of the call; nothing is
persisted or logged in plaintext (only id prefixes / message ids are logged).
"""

from typing import Optional

from utils.config import settings
from utils.logger import get_logger
from services.graph.token_manager import graph_post
from services.graph.senders.errors import GraphSendError, classify_graph_error

logger = get_logger("services.graph.senders.teams")


class TeamsSender:

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def resolve_one_on_one_chat(self, recipient_id: str) -> str:
        """
        Create or reuse a one-on-one chat between the service account and the
        recipient (AAD user id or UPN). Returns the chat id.

        POST /chats with chatType=oneOnOne is idempotent server-side: Graph
        returns the existing chat if one already exists between the two members,
        so this is safe to call before every send (no duplicate chats).
        """
        if not self.upn:
            raise RuntimeError("GRAPH_SERVICE_UPN not configured.")

        body = {
            "chatType": "oneOnOne",
            "members": [
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{self.upn}')",
                },
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{recipient_id}')",
                },
            ],
        }
        try:
            data = graph_post("chats", json_body=body)
        except Exception as exc:
            raise classify_graph_error(exc) from exc

        chat_id = data.get("id")
        if not chat_id:
            raise GraphSendError("no_chat_id", "Graph POST /chats returned no chat id")
        logger.info(f"TeamsSender: resolved one-on-one chat {chat_id[:12]}…")
        return chat_id

    def send_message(self, chat_id: str, body_html: str) -> dict:
        """
        POST /chats/{chat-id}/messages. Returns {"message_id": <id>}.
        Message content is never logged.
        """
        body = {"body": {"contentType": "html", "content": body_html}}
        try:
            data = graph_post(f"chats/{chat_id}/messages", json_body=body)
        except Exception as exc:
            raise classify_graph_error(exc) from exc

        message_id = data.get("id")
        logger.info(f"TeamsSender: sent message to chat {chat_id[:12]}… id={message_id}")
        return {"message_id": message_id}
