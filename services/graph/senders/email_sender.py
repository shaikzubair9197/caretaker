"""
EmailSender — send an Outlook email via Microsoft Graph (write side).

Endpoint: POST /users/{upn}/sendMail   (returns 202 Accepted, no body)

Pure Graph I/O — no DB, no vault, no masking — mirroring the read-side
EmailConnector. The executor decrypts the recipient address and hands plaintext
here only for the duration of the call; the recipient address is never logged.
"""

from typing import Optional

from utils.config import settings
from utils.logger import get_logger
from services.graph.token_manager import graph_post
from services.graph.senders.errors import classify_graph_error

logger = get_logger("services.graph.senders.email")


class EmailSender:

    def __init__(self, upn: Optional[str] = None) -> None:
        self.upn = upn or settings.GRAPH_SERVICE_UPN

    def send_mail(self, to_email: str, subject: str, body_html: str) -> dict:
        """
        POST /users/{upn}/sendMail. sendMail returns 202 with no body, so there
        is no message id to return — {"sent": True} on success. The recipient
        address and body content are never logged.
        """
        if not self.upn:
            raise RuntimeError("GRAPH_SERVICE_UPN not configured.")

        body = {
            "message": {
                "subject": subject,
                "body": {"contentType": "html", "content": body_html},
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            },
            "saveToSentItems": True,
        }
        try:
            graph_post(f"users/{self.upn}/sendMail", json_body=body)
        except Exception as exc:
            raise classify_graph_error(exc) from exc

        logger.info(f"EmailSender: sendMail accepted via {self.upn}")
        return {"sent": True}
