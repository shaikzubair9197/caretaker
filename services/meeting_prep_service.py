"""
MeetingPrepService — deterministic meeting-prep builder.

Per the Caretaker masking policy, Meeting Prep involves NO AI/LLM, so it reads
the plaintext source archive (`source_items.raw_text` + `source_items.metadata_`)
at full fidelity and never touches the masked projections or the vault.

Phase 1: title, time, organizer, join link, agenda for meetings starting soon.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from database.models import CalendarEvent, SourceItem
from services.preprocessing_service import PreprocessingService
from utils.config import settings
from utils.logger import get_logger
from utils.time_utils import utcnow

logger = get_logger("services.meeting_prep")


def _self_upn() -> str:
    return (settings.GRAPH_SERVICE_UPN or "").strip().lower()


def _fmt_dt(dt_str: Optional[str], tz: Optional[str]) -> Optional[str]:
    """Human-friendly display of the invite's own local time. No tz conversion."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        label = dt.strftime("%a %d %b, %H:%M")
        return f"{label} ({tz})" if tz else label
    except Exception:
        return dt_str


def _fmt_local(dt: Optional[datetime]) -> Optional[str]:
    """
    Format a naive-UTC datetime in the host's local timezone for display.
    Event times are stored as naive UTC; showing them in local time matches what
    the user sees in Outlook (e.g. 14:30 UTC -> 20:00 IST) and avoids UTC confusion.
    """
    if dt is None:
        return None
    try:
        return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%a %d %b, %H:%M %Z")
    except Exception:
        return None


# Teams appends a fixed meeting-join block to invite bodies; strip it from the agenda.
_AGENDA_CUT_MARKERS = (
    "____",
    "Microsoft Teams meeting",
    "Join on your computer",
    "Join the meeting now",
)


def _clean_agenda(text: str) -> str:
    """Remove the Teams join boilerplate so the agenda shows only real content."""
    if not text:
        return text
    cut = len(text)
    for marker in _AGENDA_CUT_MARKERS:
        i = text.find(marker)
        if i != -1:
            cut = min(cut, i)
    return text[:cut].strip()


def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp to a naive UTC datetime (matches normalizer)."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _days_ago(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    days = (utcnow().date() - dt.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _email_participants(raw: dict) -> tuple[str, str, set[str]]:
    """Return (from_address_lower, from_name, all_participant_addresses_lower)."""
    from_ea = (raw.get("from", {}) or {}).get("emailAddress", {}) or {}
    from_addr = (from_ea.get("address") or "").strip().lower()
    from_name = from_ea.get("name") or from_ea.get("address") or ""

    addrs: set[str] = set()
    if from_addr:
        addrs.add(from_addr)
    for key in ("toRecipients", "ccRecipients"):
        for r in raw.get(key, []) or []:
            a = ((r.get("emailAddress") or {}).get("address") or "").strip().lower()
            if a:
                addrs.add(a)
    return from_addr, from_name, addrs


# Document reference extraction (deterministic regex — no AI, no indexing).
# Conservative: a contiguous filename token (word chars, hyphen, underscore) plus
# a known document extension. No internal spaces, so we never grab whole sentences.
_FILE_RE = re.compile(
    r"\b[\w\-]+\.(?:docx|pptx|xlsx|pdf|doc|ppt|xls)\b", re.I
)
_LINK_RE = re.compile(
    r"https?://(?:[^\s]*sharepoint\.com|1drv\.ms)/[^\s)>\]]+", re.I
)
# Teams invite bodies embed the join link even when Graph's onlineMeetingUrl is null.
_JOIN_RE = re.compile(
    r"https://teams\.microsoft\.com/l/meetup-join/[^\s)>\]\"']+", re.I
)


def _extract_doc_refs(text: str) -> list[dict]:
    """Find explicit document links and filenames in plaintext. De-duplicated."""
    if not text:
        return []
    refs: list[dict] = []
    seen: set[str] = set()

    for m in _LINK_RE.finditer(text):
        url = m.group(0).rstrip(".,);]>")
        key = url.lower()
        if key not in seen:
            seen.add(key)
            refs.append({"label": url, "kind": "link", "url": url})

    # Remove URL spans so a filename inside a link isn't double-counted.
    remainder = _LINK_RE.sub(" ", text)
    for m in _FILE_RE.finditer(remainder):
        name = m.group(0).strip()
        key = name.lower()
        if key not in seen:
            seen.add(key)
            refs.append({"label": name, "kind": "file_ref", "url": None})
    return refs


def _matched_email_rows(
    db: Session, attendee_set: set[str], within_days: int
) -> list[tuple[int, datetime, dict]]:
    """
    Email raw payloads overlapping the attendee set, ranked by (overlap, recency).
    Shared by related_emails (display) and documents (reference extraction).
    """
    if not attendee_set:
        return []
    cutoff = utcnow() - timedelta(days=within_days)
    out: list[tuple[int, datetime, dict]] = []
    for row in db.query(SourceItem).filter(
        SourceItem.source_type == "outlook_email"
    ).all():
        if not row.raw_text:
            continue
        try:
            raw = json.loads(row.raw_text)
        except (ValueError, TypeError):
            continue
        _, _, participants = _email_participants(raw)
        if not (participants & attendee_set):
            continue
        received = _parse_iso(raw.get("receivedDateTime"))
        if received is not None and received < cutoff:
            continue
        out.append((len(participants & attendee_set), received or datetime.min, raw))
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return out


class MeetingPrepService:

    @staticmethod
    def get_upcoming(
        db: Session,
        within_minutes: int = 15,
        force: bool = False,
    ) -> list[CalendarEvent]:
        """
        Calendar events that should be prepped.

        Normal: events whose start is in (now, now+within_minutes].
        Force (demo): the soonest events not yet ended, ignoring the window.
        Cancelled events are always excluded here; self-declined are filtered in
        build_snapshot (needs the raw payload).
        """
        now = utcnow()
        q = db.query(CalendarEvent).filter(CalendarEvent.is_cancelled == False)  # noqa: E712

        if force:
            q = q.filter(CalendarEvent.end_at >= now)
        else:
            horizon = now + timedelta(minutes=within_minutes)
            q = q.filter(CalendarEvent.start_at >= now, CalendarEvent.start_at <= horizon)

        return q.order_by(CalendarEvent.start_at.asc()).all()

    @staticmethod
    def get_recently_completed_needing_transcript(
        db: Session,
        within_minutes: int = 30,
    ) -> list[CalendarEvent]:
        """
        Online meetings that ended within the lookback window and don't yet
        have an associated transcript.

        Matching is heuristic (overlapping start/end times) because there is
        no shared identifier between CalendarEvent and MeetingTranscript —
        Graph's onlineMeeting id and the calendar event id are different ID
        spaces. Exact linkage is deferred to when a real GraphTranscriptProvider
        is implemented; for now this is enough to drive the scheduler's poll.
        """
        from database.models import MeetingTranscript

        now = utcnow()
        cutoff = now - timedelta(minutes=within_minutes)
        events = (
            db.query(CalendarEvent)
            .filter(
                CalendarEvent.is_cancelled == False,       # noqa: E712
                CalendarEvent.is_online_meeting == True,    # noqa: E712
                CalendarEvent.end_at <= now,
                CalendarEvent.end_at >= cutoff,
            )
            .all()
        )

        pending = []
        for event in events:
            overlap = (
                db.query(MeetingTranscript)
                .filter(
                    MeetingTranscript.meeting_start <= event.end_at,
                    MeetingTranscript.meeting_end >= event.start_at,
                )
                .first()
            )
            if overlap is None:
                pending.append(event)
        return pending

    @staticmethod
    def build_snapshot(db: Session, event: CalendarEvent) -> Optional[dict]:
        """
        Build the un-masked prep snapshot for one event from the plaintext archive.
        Returns None (fail-closed) for cancelled or self-declined meetings, or when
        the raw payload is missing/unparseable. Checks both the DB flag and the raw
        payload so a directly-requested event can never leak a cancelled meeting.
        """
        if event.is_cancelled:
            return None

        source = (
            db.query(SourceItem).filter(SourceItem.id == event.source_id).first()
            if event.source_id else None
        )
        if source is None or not source.raw_text:
            logger.warning(f"No SourceItem/raw_text for calendar event {event.external_id}")
            return None

        try:
            raw = json.loads(source.raw_text)
        except (ValueError, TypeError):
            logger.warning(f"Unparseable raw_text for calendar event {event.external_id}")
            return None

        if raw.get("isCancelled"):
            return None

        attendees = []
        self_declined = False
        for a in raw.get("attendees", []) or []:
            ea = (a.get("emailAddress") or {})
            addr = (ea.get("address") or "").strip()
            response = ((a.get("status") or {}).get("response") or "none")
            attendees.append({
                "name": ea.get("name") or addr,
                "address": addr,
                "response": response,
            })
            if addr.lower() == _self_upn() and response == "declined":
                self_declined = True
        if self_declined:
            return None

        organizer_ea = (raw.get("organizer", {}) or {}).get("emailAddress", {}) or {}
        body = (raw.get("body", {}) or {}).get("content", "") or ""

        minutes_until = None
        if event.start_at:
            minutes_until = max(0, int((event.start_at - utcnow()).total_seconds() // 60))

        full_agenda = PreprocessingService.clean_html(body)
        attendee_addresses = [a["address"] for a in attendees]

        # Extract the Teams join link from the full body BEFORE trimming boilerplate.
        join_url = event.meeting_url or raw.get("onlineMeetingUrl")
        if not join_url:
            m = _JOIN_RE.search(full_agenda) or _JOIN_RE.search(body)
            if m:
                join_url = m.group(0)
        agenda = _clean_agenda(full_agenda)

        return {
            "external_id": event.external_id,
            "title": raw.get("subject") or "(no subject)",
            "start_display": _fmt_local(event.start_at),
            "end_display": _fmt_local(event.end_at),
            "minutes_until": minutes_until,
            "organizer": {
                "name": organizer_ea.get("name") or organizer_ea.get("address") or "",
                "address": organizer_ea.get("address") or "",
            },
            "join_url": join_url,
            "is_online_meeting": bool(event.is_online_meeting or join_url),
            "agenda": agenda,
            "attendees": attendees,
            "related_emails": MeetingPrepService.related_emails(db, attendee_addresses),
            "documents": MeetingPrepService.documents(db, raw, attendee_addresses, agenda, event_id=event.external_id),
            "related_content": MeetingPrepService._related_content(
                db, raw.get("subject") or "", agenda, attendee_addresses, event.start_at
            ),
        }

    @staticmethod
    def _related_content(
        db: Session,
        subject: str,
        agenda: str,
        attendee_addresses: list[str],
        start_at: Optional[datetime],
    ) -> list[dict]:
        """Ranked, explainable documents from the Content Retrieval layer (Phase 2).

        Fail-closed and fully isolated from the deterministic `documents()` path:
        any failure (retrieval unavailable, empty catalog, embedder down) returns
        [] so meeting prep never breaks. Imported lazily so meeting prep has no
        hard import-time dependency on the content layer. Only `type=="document"`
        results are surfaced (the API is generically typed — rule #8)."""
        try:
            from services.content_retrieval import build_meeting_query, retrieve

            query = build_meeting_query(
                title=subject,
                agenda=agenda or "",
                attendees=attendee_addresses,
                date=start_at,
            )
            results = retrieve(db, query)
            return [r for r in results if r.get("type") == "document"]
        except Exception as e:  # noqa: BLE001 - retrieval must never break meeting prep
            logger.warning(f"related_content retrieval failed (non-fatal): {e}")
            return []

    @staticmethod
    def related_emails(
        db: Session,
        attendee_addresses: list[str],
        within_days: int = 30,
        max_items: int = 3,
    ) -> list[dict]:
        """
        Emails that overlap the meeting's attendees, ranked by overlap then recency.

        Conservative (fail-closed): only emails sharing >=1 attendee are kept; the
        service-account self UPN is excluded so it doesn't match everything. Reads
        plaintext from source_items (vault tokens cannot match people across sources).
        Returns [] when there is no genuine overlap.
        """
        attendee_set = {
            (a or "").strip().lower()
            for a in attendee_addresses
            if a and a.strip().lower() != _self_upn()
        }
        rows = _matched_email_rows(db, attendee_set, within_days)

        out: list[dict] = []
        for overlap, received, raw in rows[:max_items]:
            from_addr, from_name, _ = _email_participants(raw)
            received_dt = received if received != datetime.min else None
            if from_addr in attendee_set:
                reason = f"from {from_name}, {_days_ago(received_dt)}".rstrip(", ")
            else:
                reason = f"thread includes {overlap} attendee{'s' if overlap != 1 else ''}"
            raw_body = (raw.get("body") or {}).get("content")
            if not isinstance(raw_body, str):
                raw_body = ""
            body_preview = PreprocessingService.clean_html(raw_body).strip()
            out.append({
                "subject": raw.get("subject") or "(no subject)",
                "from": from_name,
                "received": _fmt_dt(raw.get("receivedDateTime"), None),
                "reason": reason,
                "message_id": raw.get("id"),
                "body_preview": body_preview,
            })
        return out

    @staticmethod
    def documents(
        db: Session,
        raw: dict,
        attendee_addresses: list[str],
        agenda: str,
        event_id: Optional[str] = None,
        within_days: int = 30,
        max_items: int = 3,
    ) -> dict:
        """
        Document retrieval with honest confidence tiers (deterministic, no AI):
          - HIGH: exactly one strong signal (invite attachment / agenda reference)
                  -> surface that exact document.
          - LOW:  several strong signals, or references found only in related emails
                  -> up to max_items candidates with reasons.
          - NONE: no attachment and no confident reference -> empty (fail-closed).

        Precedence when the same doc appears twice: attachment > agenda > email.
        """
        attachments = []
        for a in (raw.get("attachments") or []):
            if not a.get("name"):
                continue
            attachment_url = None
            if event_id and a.get("id"):
                attachment_url = f"/meeting/prep/attachment/{event_id}/{a['id']}"
            else:
                attachment_url = a.get("url")
            attachments.append({
                "label": a["name"],
                "kind": "attachment",
                "url": attachment_url,
                "reason": "attached to invite",
            })
        invite_refs = [
            dict(r, reason="named in the agenda") for r in _extract_doc_refs(agenda or "")
        ]

        attendee_set = {
            (a or "").strip().lower()
            for a in attendee_addresses
            if a and a.strip().lower() != _self_upn()
        }
        email_refs: list[dict] = []
        for _, _, em_raw in _matched_email_rows(db, attendee_set, within_days):
            _, from_name, _ = _email_participants(em_raw)
            body = PreprocessingService.clean_html(
                (em_raw.get("body", {}) or {}).get("content", "") or ""
            )
            for r in _extract_doc_refs(body):
                email_refs.append(dict(r, reason=f"linked in email from {from_name}"))

        def _dedup(items: list[dict], seen: set[str]) -> list[dict]:
            kept = []
            for it in items:
                key = (it["url"] or it["label"]).lower()
                if key in seen:
                    continue
                seen.add(key)
                kept.append(it)
            return kept

        seen: set[str] = set()
        strong = _dedup(attachments + invite_refs, seen)
        weak = _dedup(email_refs, seen)

        if len(strong) == 1:
            return {"confidence": "HIGH", "items": [strong[0]]}
        items = (strong + weak)[:max_items]
        if items:
            return {"confidence": "LOW", "items": items}
        return {"confidence": "NONE", "items": []}

    @staticmethod
    def next_snapshot(
        db: Session,
        within_minutes: int = 15,
        force: bool = False,
    ) -> Optional[dict]:
        """First upcoming event that yields a displayable snapshot, else None."""
        for event in MeetingPrepService.get_upcoming(db, within_minutes, force):
            snap = MeetingPrepService.build_snapshot(db, event)
            if snap is not None:
                return snap
        return None

    @staticmethod
    def snapshot_for(db: Session, external_id: str) -> Optional[dict]:
        """Snapshot for a specific event id (used by the scheduler-launched popup)."""
        event = (
            db.query(CalendarEvent)
            .filter(CalendarEvent.external_id == external_id)
            .first()
        )
        if event is None:
            return None
        return MeetingPrepService.build_snapshot(db, event)
