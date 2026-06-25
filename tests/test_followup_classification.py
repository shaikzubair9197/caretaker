"""
Tests for the Phase 3 assignee-based Action Item vs Commitment classification.

Pure-logic tests (no DB): exercises the classifier, the counterparty normalizer,
self-token resolution, and that the DTO serializes followup_category.
"""

from types import SimpleNamespace

from api.drafts import _draft_dto
from services.draft_generation_service import _classify_followup, _classify_followup_category
from services.meeting_intelligence_model import _normalize_counterparty
from services.transcript_ingestion_service import _resolve_self_token
from utils.config import settings

# A meeting: SELF (caretaker) + Manager are participants; Suraj is NOT present.
SELF = "S1_SPEAKER_1"
MANAGER = "S1_SPEAKER_2"
SURAJ = "S1_PERSON_9"
RAHUL = "S1_PERSON_8"


def _transcript(self_token=SELF, participants=(SELF, MANAGER)):
    return SimpleNamespace(participant_tokens=list(participants), self_token=self_token)


def _item(owner=None, counterparty=None):
    return SimpleNamespace(owner_token=owner, extra_data={"counterparty": counterparty} if counterparty else {})


# ── The four canonical spec examples ─────────────────────────────────────────

def test_send_me_the_api_key_is_action_item():
    # "Can you send me the OpenAI API key?" — owner = self, recipient = Manager (a participant)
    item = _item(owner=SELF, counterparty={"token": MANAGER, "name": None, "kind": "person"})
    assert _classify_followup_category(item, _transcript()) == "action_item"


def test_deploy_by_friday_is_action_item():
    # "deploy by Friday" — owner = self, no external party
    item = _item(owner=SELF)
    assert _classify_followup_category(item, _transcript()) == "action_item"


def test_send_suraj_the_api_key_is_commitment():
    # "send Suraj the API key" — owner = self (does the sending), recipient = Suraj (absent)
    item = _item(owner=SELF, counterparty={"token": SURAJ, "name": None, "kind": "person"})
    assert _classify_followup_category(item, _transcript()) == "commitment"


def test_share_with_devops_team_is_commitment():
    # "share with the DevOps team" — named external entity
    item = _item(owner=SELF, counterparty={"token": None, "name": "DevOps", "kind": "team"})
    assert _classify_followup_category(item, _transcript()) == "commitment"


# ── Edge cases ───────────────────────────────────────────────────────────────

def test_external_owner_is_commitment():
    # "Rahul will send the report" — owner is a non-participant
    assert _classify_followup_category(_item(owner=RAHUL), _transcript()) == "commitment"


def test_counterparty_that_is_a_participant_stays_action_item():
    item = _item(owner=SELF, counterparty={"token": MANAGER, "name": None, "kind": "person"})
    assert _classify_followup_category(item, _transcript()) == "action_item"


def test_bracketed_tokens_are_normalized():
    item = _item(owner="<S1_SPEAKER_1>")
    t = SimpleNamespace(participant_tokens=["<S1_SPEAKER_1>", MANAGER], self_token="<S1_SPEAKER_1>")
    assert _classify_followup_category(item, t) == "action_item"


def test_unassigned_falls_back_to_action_item():
    assert _classify_followup_category(_item(owner=None), _transcript()) == "action_item"


def test_missing_self_token_does_not_crash():
    # self not in meeting → external owners still become commitments, else action_item
    assert _classify_followup_category(_item(owner=MANAGER), _transcript(self_token=None)) == "action_item"
    assert _classify_followup_category(_item(owner=SURAJ), _transcript(self_token=None)) == "commitment"


# ── counterparty normalizer ──────────────────────────────────────────────────

def test_normalize_counterparty_forms():
    assert _normalize_counterparty(None) is None
    assert _normalize_counterparty("") is None
    assert _normalize_counterparty("<S1_PERSON_9>") == {"token": "S1_PERSON_9", "name": None, "kind": "person"}
    assert _normalize_counterparty("DevOps team") == {"token": None, "name": "DevOps team", "kind": "group"}
    assert _normalize_counterparty({"name": "Finance", "kind": "department"}) == \
        {"token": None, "name": "Finance", "kind": "department"}
    assert _normalize_counterparty({"token": "<X_1>"}) == {"token": "X_1", "name": None, "kind": "person"}
    assert _normalize_counterparty({}) is None


# ── self-token resolution at ingestion ───────────────────────────────────────

class _Participant:
    def __init__(self, email, key):
        self.email = email
        self._key = key

    def identity_key(self):
        return self._key


def test_resolve_self_token_matches_sender_identity():
    scoped = {"alice": "S5_SPEAKER_1", "bob": "S5_SPEAKER_2"}
    transcript = SimpleNamespace(
        participants=[_Participant(settings.SENDER_IDENTITY, "alice"), _Participant("bob@x.com", "bob")],
        utterances=[],
    )
    assert _resolve_self_token(transcript, scoped) == "S5_SPEAKER_1"


def test_resolve_self_token_none_when_absent():
    scoped = {"bob": "S5_SPEAKER_2"}
    transcript = SimpleNamespace(participants=[_Participant("bob@x.com", "bob")], utterances=[])
    assert _resolve_self_token(transcript, scoped) is None


# ── classification metadata (reason / version / normalized counterparty) ─────

def test_classify_followup_emits_reason_version_counterparty():
    r = _classify_followup(_item(owner=SELF, counterparty={"token": SURAJ, "name": None, "kind": "person"}), _transcript())
    assert r["followup_category"] == "commitment"
    assert r["classification_reason"] == "external_counterparty"
    assert r["classification_version"] == 1
    assert r["counterparty"] == {"kind": "person", "token": SURAJ, "name": None, "is_participant": False}

    assert _classify_followup(_item(owner=RAHUL), _transcript())["classification_reason"] == "external_owner"
    assert _classify_followup(_item(owner=SELF), _transcript())["classification_reason"] == "assigned_to_self"
    assert _classify_followup(_item(owner=None), _transcript())["classification_reason"] == "fallback"


def test_normalized_counterparty_is_participant_flag():
    r = _classify_followup(_item(owner=SELF, counterparty={"token": MANAGER, "name": None, "kind": "person"}), _transcript())
    assert r["counterparty"]["is_participant"] is True
    r2 = _classify_followup(_item(owner=SELF, counterparty={"token": None, "name": "DevOps", "kind": "team"}), _transcript())
    assert r2["counterparty"] == {"kind": "team", "token": None, "name": "DevOps", "is_participant": False}


# ── DTO serialization ────────────────────────────────────────────────────────

def test_draft_dto_serializes_followup_category():
    action = SimpleNamespace(
        id=1, action_type="email_draft", status="pending",
        payload={"followup_category": "commitment", "display_title": "x", "draft_type": "email"},
        created_at=None, approved_at=None,
    )
    assert _draft_dto(action)["followup_category"] == "commitment"
