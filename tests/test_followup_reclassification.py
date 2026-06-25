"""
Phase 3 refinement #4 — proves followup_category is DERIVED STATE, recomputed from
the current meeting context on regenerate (never a stale cached value).

Scenario: "Send Rahul the API key."
  - Initially Rahul IS a participant  → Action Item (assigned_to_self, recipient present).
  - Rahul leaves the meeting + regenerate → Commitment (external_counterparty).

The draft-type classifier, retrieval, and LLM are mocked so the test isolates the
Action Item ↔ Commitment recomputation.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.draft_generation_service as dgs
from database.models import AgentAction, AuditEvent, Base, KnowledgeItem, MeetingTranscript

SELF = "S1_SPEAKER_1"
RAHUL = "S1_SPEAKER_2"


@event.listens_for(AuditEvent, "before_insert")
def _assign_audit_event_id(mapper, connection, target):
    if target.id is None:
        target.id = (connection.execute(select(func.coalesce(func.max(AuditEvent.id), 0))).scalar() or 0) + 1


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    # Isolate the category recomputation from draft-type/LLM/retrieval machinery.
    monkeypatch.setattr(dgs, "_classify", lambda item, db, recipient_token=None: ("teams_message", "teams_message_draft", False, True))
    monkeypatch.setattr(dgs.knowledge_retrieval_service, "retrieve",
                        lambda *a, **k: {"results": [{"confidence": 0.9, "value_ref": None}], "conflict": False})
    monkeypatch.setattr(dgs.LLMService, "generate_draft",
                        lambda *a, **k: SimpleNamespace(succeeded=True, data={"body": "Here is the key."}, status="LLM_OK"))
    try:
        yield session
    finally:
        session.close()


def _seed(db, participants):
    mt = MeetingTranscript(source_id=100, external_id="m1", meeting_id="m1",
                           participant_tokens=list(participants), self_token=SELF)
    db.add(mt)
    db.flush()
    ki = KnowledgeItem(
        source_id=100, knowledge_type="action_item", title_masked="Send the API key",
        owner_token=SELF, extra_data={"counterparty": {"token": RAHUL, "name": None, "kind": "person"}},
        status="open", is_active=True, knowledge_key="k1", version=1,
    )
    db.add(ki)
    db.flush()
    action = AgentAction(action_type="teams_message_draft", status="pending",
                         payload={"knowledge_item_id": ki.id, "edit_history": []}, user_id=1)
    db.add(action)
    db.commit()
    return mt, action


def test_category_recomputed_when_participant_leaves(db):
    mt, action = _seed(db, participants=[SELF, RAHUL])

    # Rahul present → recipient is a participant → Action Item.
    a1 = dgs.DraftGenerationService.regenerate(action.id, db)
    assert a1.payload["followup_category"] == "action_item"
    assert a1.payload["classification_reason"] == "assigned_to_self"

    # Rahul leaves the meeting; regenerate must re-derive from the NEW context.
    mt.participant_tokens = [SELF]
    db.commit()

    a2 = dgs.DraftGenerationService.regenerate(action.id, db)
    assert a2.payload["followup_category"] == "commitment"
    assert a2.payload["classification_reason"] == "external_counterparty"
    assert a2.payload["counterparty"] == {"kind": "person", "token": RAHUL, "name": None, "is_participant": False}
    assert a2.payload["classification_version"] == 1
