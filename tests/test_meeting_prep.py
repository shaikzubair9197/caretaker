"""
Meeting Prep service tests (Phase 1).

Uses an in-memory SQLite DB so no PostgreSQL/Graph/LLM is required. Verifies that
build_snapshot returns UN-MASKED display fields from the plaintext source archive,
that cancelled / self-declined meetings are suppressed (fail-closed), and that the
upcoming-window / force boundary math is correct.

Run with:
    cd caretaker
    python -m pytest tests/test_meeting_prep.py -v
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, CalendarEvent, SourceItem
from services.meeting_prep_service import MeetingPrepService
from utils.config import settings
from utils.time_utils import utcnow

_FIXTURE = (
    Path(__file__).parent.parent
    / "phase2_validation" / "results"
    / "calendars_01_architecture_design_review.json"
)


def _raw_payload() -> dict:
    data = json.loads(_FIXTURE.read_text())
    # The fixture stores the original Graph payload under raw_payload.
    return data["raw_payload"]


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _seed_event(db, raw: dict, *, start_in_minutes: int, cancelled: bool = False) -> CalendarEvent:
    start_at = utcnow() + timedelta(minutes=start_in_minutes)
    end_at = start_at + timedelta(minutes=60)
    source = SourceItem(
        source_type="calendar",
        raw_text=json.dumps(raw),
        metadata_={"external_id": raw["id"], "subject": raw.get("subject")},
    )
    db.add(source)
    db.flush()
    event = CalendarEvent(
        source_id=source.id,
        external_id=raw["id"],
        subject_masked="<MASKED>",  # proves snapshot does NOT read the masked field
        start_at=start_at,
        end_at=end_at,
        is_online_meeting=bool(raw.get("isOnlineMeeting")),
        meeting_url=raw.get("onlineMeetingUrl"),
        is_cancelled=cancelled,
    )
    db.add(event)
    db.commit()
    return event


def test_snapshot_uses_unmasked_plaintext(db):
    raw = _raw_payload()
    event = _seed_event(db, raw, start_in_minutes=10)

    snap = MeetingPrepService.build_snapshot(db, event)

    assert snap is not None
    # Title is the real subject, not the masked projection.
    assert snap["title"] == raw["subject"]
    assert "<" not in snap["title"]
    assert snap["organizer"]["name"] == raw["organizer"]["emailAddress"]["name"]
    assert snap["join_url"] == raw["onlineMeetingUrl"]
    # Agenda is HTML-cleaned but un-masked (real words survive).
    assert "token refresh architecture" in snap["agenda"]
    assert "<API_KEY" not in snap["agenda"]
    assert len(snap["attendees"]) == len(raw["attendees"])


def test_cancelled_event_suppressed(db):
    raw = _raw_payload()
    event = _seed_event(db, raw, start_in_minutes=10, cancelled=True)
    assert MeetingPrepService.build_snapshot(db, event) is None


def test_raw_cancelled_flag_suppressed(db):
    raw = _raw_payload()
    raw["isCancelled"] = True
    # DB column says not cancelled, but the raw payload says it is — fail closed.
    event = _seed_event(db, raw, start_in_minutes=10, cancelled=False)
    assert MeetingPrepService.build_snapshot(db, event) is None


def test_self_declined_suppressed(db, monkeypatch):
    raw = _raw_payload()
    self_addr = raw["attendees"][0]["emailAddress"]["address"]
    raw["attendees"][0]["status"]["response"] = "declined"
    monkeypatch.setattr(settings, "GRAPH_SERVICE_UPN", self_addr)
    event = _seed_event(db, raw, start_in_minutes=10)
    assert MeetingPrepService.build_snapshot(db, event) is None


def test_get_upcoming_window_boundary(db):
    raw = _raw_payload()
    in_window = _seed_event(db, dict(raw, id="ev-in"), start_in_minutes=10)
    _seed_event(db, dict(raw, id="ev-out"), start_in_minutes=40)

    upcoming = MeetingPrepService.get_upcoming(db, within_minutes=15, force=False)
    ids = {e.external_id for e in upcoming}
    assert in_window.external_id in ids
    assert "ev-out" not in ids


def test_force_ignores_window(db):
    raw = _raw_payload()
    _seed_event(db, dict(raw, id="ev-far"), start_in_minutes=120)
    upcoming = MeetingPrepService.get_upcoming(db, within_minutes=15, force=True)
    assert any(e.external_id == "ev-far" for e in upcoming)


def test_next_snapshot_skips_suppressed(db):
    raw = _raw_payload()
    # Soonest event is cancelled; next one is valid — next_snapshot must skip the first.
    _seed_event(db, dict(raw, id="ev-cancel"), start_in_minutes=5, cancelled=True)
    _seed_event(db, dict(raw, id="ev-ok"), start_in_minutes=10)
    snap = MeetingPrepService.next_snapshot(db, within_minutes=15, force=False)
    assert snap is not None
    assert snap["external_id"] == "ev-ok"


# ── Phase 2: related emails ────────────────────────────────────────────────────

def _seed_email(db, *, eid, subject, from_addr, from_name, to, cc=None, days_old=1, body="body"):
    received = (utcnow() - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = {
        "id": eid,
        "subject": subject,
        "from": {"emailAddress": {"name": from_name, "address": from_addr}},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in (cc or [])],
        "receivedDateTime": received,
        "body": {"content": body},
    }
    db.add(SourceItem(source_type="outlook_email", raw_text=json.dumps(raw),
                      metadata_={"external_id": eid}))
    db.commit()


ATT1 = "monish.selvanathan@amperatech.ai"
ATT2 = "deepak.k@amperatech.ai"
OTHER = "stranger@external.com"


def test_related_email_requires_overlap(db):
    _seed_email(db, eid="e1", subject="Sprint 47", from_addr=ATT2, from_name="Deepak",
                to=[ATT1])
    _seed_email(db, eid="e2", subject="Unrelated newsletter", from_addr=OTHER,
                from_name="Spam", to=[OTHER])
    out = MeetingPrepService.related_emails(db, [ATT1, ATT2])
    subjects = [e["subject"] for e in out]
    assert "Sprint 47" in subjects
    assert "Unrelated newsletter" not in subjects


def test_related_email_excludes_self_only_match(db, monkeypatch):
    # An email that only overlaps via the service-account self UPN must NOT match.
    monkeypatch.setattr(settings, "GRAPH_SERVICE_UPN", "care.taker@amperatech.ai")
    _seed_email(db, eid="e1", subject="Self only", from_addr="care.taker@amperatech.ai",
                from_name="Care Taker", to=["care.taker@amperatech.ai"])
    out = MeetingPrepService.related_emails(db, ["care.taker@amperatech.ai"])
    assert out == []


def test_related_email_ranking_and_cap(db):
    # Higher overlap and more-recent should rank first; cap at max_items=3.
    _seed_email(db, eid="hi", subject="Both attendees", from_addr=ATT2, from_name="Deepak",
                to=[ATT1], days_old=5)
    _seed_email(db, eid="lo", subject="One attendee old", from_addr=ATT1, from_name="Monish",
                to=[OTHER], days_old=10)
    for i in range(3):
        _seed_email(db, eid=f"x{i}", subject=f"filler{i}", from_addr=ATT1,
                    from_name="Monish", to=[OTHER], days_old=20 + i)
    out = MeetingPrepService.related_emails(db, [ATT1, ATT2], max_items=3)
    assert len(out) == 3
    assert out[0]["subject"] == "Both attendees"  # overlap=2 wins
    assert "from Deepak" in out[0]["reason"]


def test_related_email_recency_window(db):
    _seed_email(db, eid="old", subject="Too old", from_addr=ATT2, from_name="Deepak",
                to=[ATT1], days_old=120)
    out = MeetingPrepService.related_emails(db, [ATT1, ATT2], within_days=30)
    assert out == []


def test_snapshot_includes_related_emails(db):
    raw = _raw_payload()
    event = _seed_event(db, raw, start_in_minutes=10)
    _seed_email(db, eid="e1", subject="Re: Architecture review", from_addr=ATT2,
                from_name="Deepak Krishnamurthy", to=[ATT1])
    snap = MeetingPrepService.build_snapshot(db, event)
    assert snap is not None
    assert any(e["subject"] == "Re: Architecture review" for e in snap["related_emails"])
    assert any(e.get("body_preview") for e in snap["related_emails"])


def test_related_email_body_preview_cleans_html(db):
    raw = _raw_payload()
    event = _seed_event(db, raw, start_in_minutes=10)
    _seed_email(
        db,
        eid="e-html",
        subject="HTML body",
        from_addr=ATT2,
        from_name="Deepak Krishnamurthy",
        to=[ATT1],
        body="<div>Hello <strong>Monish</strong></div><p>Join: <a href=\"https://teams.microsoft.com\">Teams</a></p>",
    )
    snap = MeetingPrepService.build_snapshot(db, event)
    assert snap is not None
    preview = next(
        (e.get("body_preview") for e in snap["related_emails"] if e["subject"] == "HTML body"),
        None,
    )
    assert preview is not None
    assert "Hello Monish" in preview
    assert "Teams" in preview
    assert "<div>" not in preview
    assert "<a" not in preview


# ── Phase 3: documents ─────────────────────────────────────────────────────────

def test_extract_doc_refs_files_links_and_negatives():
    from services.meeting_prep_service import _extract_doc_refs
    text = (
        "Please review Auth_Design.docx and the deck at "
        "https://contoso.sharepoint.com/sites/x/Spec.aspx before we meet. "
        "Mirror: https://1drv.ms/w/s!abc . Ignore notes.txt and version v2.1 here."
    )
    labels = [r["label"] for r in _extract_doc_refs(text)]
    assert "Auth_Design.docx" in labels
    assert any("contoso.sharepoint.com" in l for l in labels)
    assert any("1drv.ms" in l for l in labels)
    assert "notes.txt" not in labels   # .txt not a tracked document type
    assert "v2.1" not in labels        # not a document extension


def test_documents_high_single_attachment(db):
    raw = {"attachments": [{"name": "Spec.docx"}]}
    out = MeetingPrepService.documents(db, raw, [ATT1], agenda="")
    assert out["confidence"] == "HIGH"
    assert out["items"][0]["label"] == "Spec.docx"
    assert out["items"][0]["reason"] == "attached to invite"


def test_documents_high_single_agenda_reference(db):
    out = MeetingPrepService.documents(db, {}, [ATT1], agenda="Read Roadmap.pdf before the call")
    assert out["confidence"] == "HIGH"
    assert out["items"][0]["label"] == "Roadmap.pdf"
    assert out["items"][0]["reason"] == "named in the agenda"


def test_documents_low_multiple_strong(db):
    raw = {"attachments": [{"name": "A.docx"}, {"name": "B.pptx"}]}
    out = MeetingPrepService.documents(db, raw, [ATT1], agenda="also see C.xlsx")
    assert out["confidence"] == "LOW"
    assert len(out["items"]) == 3  # capped at max_items
    assert out["items"][0]["reason"] == "attached to invite"  # attachments first


def test_documents_email_only_reference_is_low(db):
    _seed_email(db, eid="e1", subject="Doc", from_addr=ATT2, from_name="Deepak",
                to=[ATT1], body="Latest is Roadmap.pptx — please skim.")
    out = MeetingPrepService.documents(db, {}, [ATT1, ATT2], agenda="")
    assert out["confidence"] == "LOW"
    assert out["items"][0]["label"] == "Roadmap.pptx"
    assert "from Deepak" in out["items"][0]["reason"]


def test_documents_none_when_weak(db):
    out = MeetingPrepService.documents(db, {}, [ATT1], agenda="just a normal agenda, no docs")
    assert out["confidence"] == "NONE"
    assert out["items"] == []


def test_documents_dedup_attachment_beats_agenda(db):
    # Same file as attachment and named in agenda -> one item, HIGH, attachment reason.
    raw = {"attachments": [{"name": "Plan.docx"}]}
    out = MeetingPrepService.documents(db, raw, [ATT1], agenda="see Plan.docx")
    assert out["confidence"] == "HIGH"
    assert len(out["items"]) == 1
    assert out["items"][0]["reason"] == "attached to invite"


def test_snapshot_includes_documents(db):
    raw = _raw_payload()
    raw["attachments"] = [{"name": "PR342_review.docx"}]
    event = _seed_event(db, raw, start_in_minutes=10)
    snap = MeetingPrepService.build_snapshot(db, event)
    assert snap["documents"]["confidence"] == "HIGH"
    assert snap["documents"]["items"][0]["label"] == "PR342_review.docx"


# ── Edge cases / robustness ────────────────────────────────────────────────────

def test_missing_source_item_returns_none(db):
    ev = CalendarEvent(source_id=999999, external_id="no-src",
                       start_at=utcnow() + timedelta(minutes=5),
                       end_at=utcnow() + timedelta(minutes=35))
    db.add(ev)
    db.commit()
    assert MeetingPrepService.build_snapshot(db, ev) is None


def test_malformed_raw_text_returns_none(db):
    src = SourceItem(source_type="calendar", raw_text="{not valid json")
    db.add(src)
    db.flush()
    ev = CalendarEvent(source_id=src.id, external_id="bad-json",
                       start_at=utcnow() + timedelta(minutes=5),
                       end_at=utcnow() + timedelta(minutes=35))
    db.add(ev)
    db.commit()
    assert MeetingPrepService.build_snapshot(db, ev) is None


def test_no_attendees_yields_empty_context(db):
    raw = _raw_payload()
    raw["attendees"] = []
    ev = _seed_event(db, raw, start_in_minutes=10)
    _seed_email(db, eid="e1", subject="x", from_addr=ATT2, from_name="D", to=[ATT1])
    snap = MeetingPrepService.build_snapshot(db, ev)
    assert snap is not None
    assert snap["attendees"] == []
    assert snap["related_emails"] == []  # no attendees -> no overlap possible


def test_empty_agenda_does_not_crash(db):
    raw = _raw_payload()
    raw["body"] = {"content": ""}
    ev = _seed_event(db, raw, start_in_minutes=10)
    snap = MeetingPrepService.build_snapshot(db, ev)
    assert snap is not None
    assert snap["agenda"] == ""
    assert snap["documents"]["confidence"] == "NONE"


def test_email_missing_received_date_does_not_crash(db):
    raw = {
        "id": "e-nodate", "subject": "No date",
        "from": {"emailAddress": {"name": "Deepak", "address": ATT2}},
        "toRecipients": [{"emailAddress": {"address": ATT1}}],
        "body": {"content": "x"},
        # no receivedDateTime
    }
    db.add(SourceItem(source_type="outlook_email", raw_text=json.dumps(raw)))
    db.commit()
    out = MeetingPrepService.related_emails(db, [ATT1, ATT2])
    assert any(e["subject"] == "No date" for e in out)


def test_window_detection_and_local_display(db):
    # Window math uses UTC start_at; display is the host-local conversion.
    raw = _raw_payload()
    ev = _seed_event(db, raw, start_in_minutes=10)
    upcoming = MeetingPrepService.get_upcoming(db, within_minutes=15, force=False)
    assert any(e.external_id == ev.external_id for e in upcoming)
    snap = MeetingPrepService.build_snapshot(db, ev)
    # Display is a non-empty, human-friendly local-time string (host tz abbrev).
    assert snap["start_display"] and "," in snap["start_display"]


def test_agenda_strips_teams_boilerplate(db):
    raw = _raw_payload()
    raw["onlineMeetingUrl"] = None  # force the body join-link fallback
    raw["body"] = {"content": (
        "Real agenda: review the API design.\n"
        "________________________________________________________________________________\n"
        "Microsoft Teams meeting\n"
        "Join on your computer: https://teams.microsoft.com/l/meetup-join/abc\n"
        "Meeting ID: 123 456 789"
    )}
    ev = _seed_event(db, raw, start_in_minutes=10)
    snap = MeetingPrepService.build_snapshot(db, ev)
    assert snap["agenda"] == "Real agenda: review the API design."
    # join link is still recovered from the (pre-clean) body
    assert snap["join_url"] == "https://teams.microsoft.com/l/meetup-join/abc"


def test_window_excludes_in_progress_in_normal_mode(db):
    # A meeting that already started (start in the past) is NOT "starting soon".
    raw = _raw_payload()
    ev = _seed_event(db, raw, start_in_minutes=-5)  # started 5 min ago, ends in 55
    upcoming = MeetingPrepService.get_upcoming(db, within_minutes=15, force=False)
    assert all(e.external_id != ev.external_id for e in upcoming)
    # but force mode (demo) still surfaces it since it hasn't ended
    forced = MeetingPrepService.get_upcoming(db, within_minutes=15, force=True)
    assert any(e.external_id == ev.external_id for e in forced)


def test_attachment_without_name_skipped(db):
    raw = {"attachments": [{"contentType": "application/pdf"}, {"name": "Real.pdf"}]}
    out = MeetingPrepService.documents(db, raw, [ATT1], agenda="")
    labels = [d["label"] for d in out["items"]]
    assert labels == ["Real.pdf"]


def test_calendar_naive_datetime_parsed_as_utc():
    """
    Graph returns calendar times as a naive dateTime in UTC. The normalizer must
    treat naive as UTC, not host-local — otherwise event times shift by the host's
    offset (the IST host bug that hid a 14:30 UTC meeting as 09:00).
    """
    from services.graph.normalizer import GraphNormalizer
    payload = {
        "id": "tz1", "subject": "TZ test",
        "start": {"dateTime": "2026-06-16T14:30:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-06-16T15:00:00.0000000", "timeZone": "UTC"},
        "organizer": {"emailAddress": {"name": "O", "address": "o@x.com"}},
        "attendees": [], "body": {"content": ""},
    }
    item = GraphNormalizer.normalize_calendar_event(payload)
    assert (item.timestamp.hour, item.timestamp.minute) == (14, 30)
    assert (item.end_time.hour, item.end_time.minute) == (15, 0)
