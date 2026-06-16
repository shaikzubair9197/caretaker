"""
GraphNormalizer — maps raw Graph API payloads to a unified internal schema.

Output: GraphSourceItem dataclass — consumed by the ingestion pipeline.
The raw payload is stored encrypted in SourceItem.raw_text (Phase 2B/C).
Only metadata fields are exposed here; body content goes through the
masking pipeline before any storage.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger("services.graph.normalizer")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string to a naive UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


@dataclass
class GraphSourceItem:
    """Unified representation of any Graph source item before ingestion."""
    source_type:  str           # outlook_email | teams_chat | teams_channel | transcript | calendar | todo
    external_id:  str           # Graph item ID (dedup key)
    raw_payload:  dict          # original API response — encrypted before storage
    body_text:    str           # plain-text content for masking pipeline
    subject:      Optional[str] = None
    sender:       Optional[str] = None
    sender_name:  Optional[str] = None
    recipients:   list          = field(default_factory=list)
    participants: list          = field(default_factory=list)
    timestamp:    Optional[datetime] = None
    end_time:     Optional[datetime] = None
    thread_id:    Optional[str] = None
    importance:   str           = "normal"
    has_attachments: bool       = False
    metadata:     dict          = field(default_factory=dict)


class GraphNormalizer:

    @staticmethod
    def normalize_email(payload: dict) -> GraphSourceItem:
        body = (payload.get("body", {}) or {}).get("content", "") or ""
        subject = payload.get("subject", "") or ""
        from_obj = (payload.get("from", {}) or {}).get("emailAddress", {}) or {}
        to_list = payload.get("toRecipients", []) or []
        cc_list = payload.get("ccRecipients", []) or []

        recipients = [
            r.get("emailAddress", {}).get("address", "") or ""
            for r in (to_list + cc_list)
            if r
        ]

        return GraphSourceItem(
            source_type     = "outlook_email",
            external_id     = payload.get("id", ""),
            raw_payload     = payload,
            body_text       = f"Subject: {subject}\n\n{body}",
            subject         = subject,
            sender          = from_obj.get("address", ""),
            sender_name     = from_obj.get("name", ""),
            recipients      = [r for r in recipients if r],
            timestamp       = _parse_dt(payload.get("receivedDateTime")),
            thread_id       = payload.get("conversationId"),
            importance      = payload.get("importance", "normal"),
            has_attachments = bool(payload.get("hasAttachments")),
            metadata        = {
                "internet_message_id": payload.get("internetMessageId"),
                "folder":              payload.get("parentFolderId"),
            },
        )

    @staticmethod
    def normalize_teams_message(payload: dict, is_channel: bool = False) -> GraphSourceItem:
        body = (payload.get("body", {}) or {}).get("content", "") or ""
        from_user = (payload.get("from", {}) or {}).get("user", {}) or {}

        mentions = [
            ((m.get("mentioned") or {}).get("user") or {}).get("displayName", "")
            for m in (payload.get("mentions") or [])
        ]

        # Real Graph chat payloads include channelIdentity:null — `.get(k, {})`
        # returns None for a present-but-null key, so coalesce with `or {}`.
        channel_identity = payload.get("channelIdentity") or {}

        return GraphSourceItem(
            source_type     = "teams_channel" if is_channel else "teams_chat",
            external_id     = payload.get("id", ""),
            raw_payload     = payload,
            body_text       = body,
            sender          = from_user.get("id", ""),
            sender_name     = from_user.get("displayName", ""),
            participants    = [m for m in mentions if m],
            timestamp       = _parse_dt(payload.get("createdDateTime")),
            thread_id       = payload.get("replyToId") or payload.get("chatId"),
            importance      = payload.get("importance", "normal"),
            has_attachments = bool(payload.get("attachments")),
            metadata        = {
                "chat_id":     payload.get("chatId"),
                "channel_id":  channel_identity.get("channelId"),
                "team_id":     channel_identity.get("teamId"),
                "message_type":payload.get("messageType", "message"),
                "reply_to_id": payload.get("replyToId"),
            },
        )

    @staticmethod
    def normalize_transcript(payload: dict) -> GraphSourceItem:
        content = payload.get("content", "") or ""
        participants = payload.get("participants", []) or []

        words = len(content.split())
        lines = [l for l in content.splitlines() if l.strip()]

        return GraphSourceItem(
            source_type  = "transcript",
            external_id  = payload.get("id", ""),
            raw_payload  = payload,
            body_text    = content,
            subject      = payload.get("subject"),
            participants = participants if isinstance(participants, list) else [],
            timestamp    = _parse_dt(payload.get("createdDateTime")),
            metadata     = {
                "meeting_id":    payload.get("meetingId"),
                "word_count":    words,
                "segment_count": len(lines),
            },
        )

    @staticmethod
    def normalize_calendar_event(payload: dict) -> GraphSourceItem:
        body = (payload.get("body", {}) or {}).get("content", "") or ""
        subject = payload.get("subject", "") or ""
        organizer = (
            (payload.get("organizer", {}) or {})
            .get("emailAddress", {})
        ) or {}
        attendees = payload.get("attendees", []) or []

        participants = [
            (a.get("emailAddress", {}) or {}).get("address", "") or ""
            for a in attendees
            if a
        ]

        return GraphSourceItem(
            source_type     = "calendar",
            external_id     = payload.get("id", ""),
            raw_payload     = payload,
            body_text       = f"Event: {subject}\n\n{body}",
            subject         = subject,
            sender          = organizer.get("address", ""),
            sender_name     = organizer.get("name", ""),
            recipients      = [p for p in participants if p],
            timestamp       = _parse_dt(
                (payload.get("start", {}) or {}).get("dateTime")
            ),
            end_time        = _parse_dt(
                (payload.get("end", {}) or {}).get("dateTime")
            ),
            importance      = payload.get("importance", "normal"),
            metadata        = {
                "is_online_meeting": payload.get("isOnlineMeeting"),
                "meeting_url":       payload.get("onlineMeetingUrl"),
                "recurrence":        payload.get("recurrence", {}) is not None,
                "is_cancelled":      payload.get("isCancelled", False),
            },
        )

    @staticmethod
    def normalize_todo_task(payload: dict) -> GraphSourceItem:
        title = payload.get("title", "") or ""
        body = (payload.get("body", {}) or {}).get("content", "") or ""

        return GraphSourceItem(
            source_type = "todo",
            external_id = payload.get("id", ""),
            raw_payload = payload,
            body_text   = f"Task: {title}\n\n{body}",
            subject     = title,
            timestamp   = _parse_dt(payload.get("createdDateTime")),
            importance  = payload.get("importance", "normal"),
            metadata    = {
                "status":   payload.get("status", "notStarted"),
                "due_at":   (payload.get("dueDateTime", {}) or {}).get("dateTime"),
                "list_id":  payload.get("listId"),
            },
        )

    @staticmethod
    def normalize(source_type: str, payload: dict) -> GraphSourceItem:
        """Dispatch normalizer by source_type string."""
        dispatch = {
            "outlook_email":  GraphNormalizer.normalize_email,
            "teams_chat":     lambda p: GraphNormalizer.normalize_teams_message(p, False),
            "teams_channel":  lambda p: GraphNormalizer.normalize_teams_message(p, True),
            "transcript":     GraphNormalizer.normalize_transcript,
            "calendar":       GraphNormalizer.normalize_calendar_event,
            "todo":           GraphNormalizer.normalize_todo_task,
        }
        fn = dispatch.get(source_type)
        if fn is None:
            raise ValueError(f"Unknown Graph source_type: {source_type!r}")
        return fn(payload)
