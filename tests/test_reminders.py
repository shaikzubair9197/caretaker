"""
Tests for reminder auto-scheduling (Follow-up Center plan — Phase 6).

In-memory SQLite (no network, no DB server). Covers the CommitmentService reminder
contract that the /reminders endpoints + meeting_scheduler daemon build on:
  - due_date snapshot → remind_at derivation (default + explicit offset)
  - no due_date → no reminder scheduled
  - get_due filtering (window, fire-once, cancelled/done excluded)
  - mark_reminded fire-once stamp
  - reschedule re-arms and re-derives

The whole point of Phase 6: the commitments table is self-contained — none of this
touches KnowledgeItem.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, Commitment
from services.commitment_service import CommitmentService
from utils.config import settings


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# A fixed "now" so window math is deterministic.
NOW = datetime(2026, 6, 25, 12, 0, 0)


def test_create_with_due_date_derives_remind_at_default_offset(db):
    due = NOW + timedelta(days=1)
    c = CommitmentService.create(db, raw_text="x", action="ship report", commitment_type="reminder", due_date=due)
    db.commit()
    assert c.due_date == due
    assert c.remind_offset_minutes == settings.REMINDER_DEFAULT_OFFSET_MINUTES
    assert c.remind_at == due - timedelta(minutes=settings.REMINDER_DEFAULT_OFFSET_MINUTES)
    assert c.reminded_at is None


def test_create_with_explicit_offset(db):
    due = NOW + timedelta(hours=3)
    c = CommitmentService.create(db, raw_text="x", action="call vendor", due_date=due, remind_offset_minutes=15)
    db.commit()
    assert c.remind_offset_minutes == 15
    assert c.remind_at == due - timedelta(minutes=15)


def test_create_without_due_date_schedules_no_reminder(db):
    c = CommitmentService.create(db, raw_text="x", action="no deadline")
    db.commit()
    assert c.due_date is None
    assert c.remind_at is None
    assert c.remind_offset_minutes is None
    assert c.reminded_at is None


def test_get_due_returns_only_due_unfired_active(db):
    # due now, unfired → included
    due_now = CommitmentService.create(db, raw_text="a", action="due now", due_date=NOW, remind_offset_minutes=0)
    # remind_at in the future → excluded
    CommitmentService.create(db, raw_text="b", action="future", due_date=NOW + timedelta(days=2))
    # no reminder at all → excluded
    CommitmentService.create(db, raw_text="c", action="none")
    db.commit()

    due = CommitmentService.get_due(db, now=NOW)
    assert [c.id for c in due] == [due_now.id]


def test_get_due_excludes_already_fired(db):
    c = CommitmentService.create(db, raw_text="a", action="x", due_date=NOW, remind_offset_minutes=0)
    db.commit()
    assert CommitmentService.get_due(db, now=NOW)  # due before firing

    CommitmentService.mark_reminded(db, c.id, when=NOW)
    db.commit()
    assert CommitmentService.get_due(db, now=NOW) == []   # fire-once: never again


def test_get_due_excludes_dismissed_and_done(db):
    c1 = CommitmentService.create(db, raw_text="a", action="x", due_date=NOW, remind_offset_minutes=0)
    c2 = CommitmentService.create(db, raw_text="b", action="y", due_date=NOW, remind_offset_minutes=0)
    c1.status = "dismissed"
    c2.status = "done"
    db.commit()
    assert CommitmentService.get_due(db, now=NOW) == []


def test_mark_reminded_unknown_id_returns_none(db):
    assert CommitmentService.mark_reminded(db, 999999) is None


def test_reschedule_with_offset_rederives_and_rearms(db):
    due = NOW + timedelta(days=1)
    c = CommitmentService.create(db, raw_text="a", action="x", due_date=due, remind_offset_minutes=60)
    db.flush()                                           # assign id (agent.py flushes too)
    CommitmentService.mark_reminded(db, c.id, when=NOW)  # pretend it already fired
    db.commit()
    assert c.reminded_at is not None

    CommitmentService.reschedule_reminder(db, c.id, remind_offset_minutes=120)
    db.commit()
    assert c.remind_offset_minutes == 120
    assert c.remind_at == due - timedelta(minutes=120)
    assert c.reminded_at is None   # re-armed → can fire again
    # re-armed reminder is due again at/after its new remind_at
    assert c in CommitmentService.get_due(db, now=due)


def test_reschedule_explicit_remind_at_wins(db):
    due = NOW + timedelta(days=1)
    c = CommitmentService.create(db, raw_text="a", action="x", due_date=due)
    db.commit()
    explicit = NOW + timedelta(hours=2)
    CommitmentService.reschedule_reminder(db, c.id, remind_at=explicit)
    db.commit()
    assert c.remind_at == explicit
    assert c.reminded_at is None


def test_reschedule_unknown_id_returns_none(db):
    assert CommitmentService.reschedule_reminder(db, 999999, remind_offset_minutes=30) is None
