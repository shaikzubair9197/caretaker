"""
Regression tests for the recipient-routing invariant (Follow-up Center).

HARD RULE: care.taker@amperatech.ai is the Caretaker service account / assistant.
It is NEVER a recipient of a generated draft. Recipient resolution must route to
the human party who should receive the deliverable — the requester when Caretaker
fulfilled a commitment — with the self/service account excluded in all cases.

These exercise the pure resolution + payload-shaping functions (no DB, no network);
the end-to-end "send goes to the requester, never the service account" path is
covered by tests/test_secure_reference_resolution.py::test_rotation_consistency_reveal_and_send.
"""

from datetime import datetime
from types import SimpleNamespace

import services.draft_generation_service as dgs


def _item(owner, counterparty_token=None, due_at=None):
    extra = {}
    if counterparty_token is not None:
        extra["counterparty"] = {"kind": "person", "token": counterparty_token}
    return SimpleNamespace(
        owner_token=owner, extra_data=extra, id=1, knowledge_key="k", version=1,
        title_masked="title", detail_masked="", due_at=due_at,
    )


def _transcript(self_token="S1_SELF", participants=("S1_PERSON_2",)):
    return SimpleNamespace(self_token=self_token, participant_tokens=list(participants))


# ── _resolve_recipient_token ──────────────────────────────────────────────────

def test_caretaker_commit_routes_to_requester():
    # Caretaker (self) committed → recipient is the requesting counterparty.
    item = _item(owner="S1_SELF", counterparty_token="S1_PERSON_2")
    assert dgs._resolve_recipient_token(item, _transcript()) == "S1_PERSON_2"


def test_caretaker_commit_without_any_other_participant_has_no_recipient():
    # Caretaker committed but it is the ONLY participant → no requester to infer,
    # so no recipient (never falls back to self).
    item = _item(owner="S1_SELF")
    t = _transcript(self_token="S1_SELF", participants=("S1_SELF",))
    assert dgs._resolve_recipient_token(item, t) is None


def test_caretaker_commit_infers_requester_named_in_text():
    # No counterparty captured, but the item names the requester as a participant
    # token → resolve to that participant (deterministic, masked-only).
    item = SimpleNamespace(
        owner_token="S514_SPEAKER_1", extra_data={},
        title_masked="Email production OpenAI key to S514_SPEAKER_2", detail_masked="",
    )
    t = _transcript(self_token="S514_SPEAKER_1",
                    participants=("S514_SPEAKER_1", "S514_SPEAKER_2"))
    assert dgs._resolve_recipient_token(item, t) == "S514_SPEAKER_2"


def test_caretaker_commit_infers_requester_in_one_on_one():
    # No counterparty, requester not named, but a 1:1 meeting → the only other party.
    item = SimpleNamespace(
        owner_token="S2_SELF", extra_data={},
        title_masked="Send the report", detail_masked="",
    )
    t = _transcript(self_token="S2_SELF", participants=("S2_SELF", "S2_PERSON_9"))
    assert dgs._resolve_recipient_token(item, t) == "S2_PERSON_9"


def test_caretaker_commit_ambiguous_group_has_no_recipient():
    # No counterparty, not named, and 3+ participants → ambiguous, no recipient
    # (never guesses, never falls back to self).
    item = SimpleNamespace(
        owner_token="S3_SELF", extra_data={},
        title_masked="Follow up on the action", detail_masked="",
    )
    t = _transcript(self_token="S3_SELF", participants=("S3_SELF", "S3_P1", "S3_P2"))
    assert dgs._resolve_recipient_token(item, t) is None


def test_external_owner_routes_to_owner():
    # Owner is an external party (not self) → recipient is the owner. Unchanged.
    item = _item(owner="S1_PERSON_2")
    assert dgs._resolve_recipient_token(item, _transcript()) == "S1_PERSON_2"


def test_recipient_never_self_even_if_counterparty_is_self():
    item = _item(owner="S1_SELF", counterparty_token="S1_SELF")
    assert dgs._resolve_recipient_token(item, _transcript(participants=("S1_SELF",))) is None


def test_tokens_are_stripped_of_angle_brackets():
    item = SimpleNamespace(
        owner_token="<S1_SELF>", extra_data={"counterparty": {"token": "<S1_PERSON_2>"}},
    )
    t = SimpleNamespace(self_token="<S1_SELF>", participant_tokens=["<S1_PERSON_2>"])
    assert dgs._resolve_recipient_token(item, t) == "S1_PERSON_2"


def test_no_transcript_falls_back_to_owner():
    # Without a transcript there is no self to exclude — owner is the recipient.
    item = _item(owner="S1_PERSON_2")
    assert dgs._resolve_recipient_token(item, None) == "S1_PERSON_2"


# ── _shape_payload routing ────────────────────────────────────────────────────

_DATA = {"subject": "s", "body": "b {{SECURE_REF:1}}", "title": "t",
         "suggestion_text": "x", "citations": []}


def test_email_payload_targets_requester_not_self():
    item = _item(owner="S1_SELF")
    p = dgs._shape_payload("email", "email_draft", item, _DATA, [], 0.9, False,
                           True, False, "S1_PERSON_2")
    assert p["recipient_token"] == "S1_PERSON_2_EMAIL"


def test_teams_payload_uses_aad_of_recipient():
    item = _item(owner="S1_SELF")
    p = dgs._shape_payload("teams_message", "teams_message_draft", item, _DATA, [], 0.9, False,
                           True, True, "S1_PERSON_2")
    assert p["recipient_token"] == "S1_PERSON_2_AAD"


def test_no_recipient_yields_null_recipient_token_no_self_leak():
    item = _item(owner="S1_SELF")
    p = dgs._shape_payload("email", "email_draft", item, _DATA, [], 0.9, False,
                           False, False, None)
    assert p["recipient_token"] is None   # never falls back to the Caretaker self


def test_calendar_attendee_excludes_self_when_no_recipient():
    item = _item(owner="S1_SELF", due_at=datetime(2026, 6, 25, 9, 0))
    p = dgs._shape_payload("calendar_reminder", "calendar_reminder_draft", item, _DATA, [], 0.9, False,
                           False, False, None)
    assert p["attendee_tokens"] == []


def test_calendar_attendee_is_recipient_when_reachable():
    item = _item(owner="S1_SELF", due_at=datetime(2026, 6, 25, 9, 0))
    p = dgs._shape_payload("calendar_reminder", "calendar_reminder_draft", item, _DATA, [], 0.9, False,
                           True, False, "S1_PERSON_2")
    assert p["attendee_tokens"] == ["S1_PERSON_2_EMAIL"]


def test_reminder_behavior_unaffected_person_token_is_owner():
    # A reminder is a LOCAL commitment (not a sent message); its person_token keeps
    # the owner label — existing behavior must be unchanged by the routing fix.
    item = _item(owner="S1_PERSON_2")
    p = dgs._shape_payload("reminder", "reminder_draft", item, _DATA, [], 0.9, False,
                           False, False, None)
    assert p["person_token"] == "S1_PERSON_2"


# ── Channel selection: requested channel wins over the default ────────────────

def _ki(title, detail=""):
    return SimpleNamespace(knowledge_type="action_item", title_masked=title,
                           detail_masked=detail, due_at=None)


def test_preferred_channel_detects_outlook_email():
    assert dgs._preferred_channel(_ki("Email production OpenAI key to recipient")) == "email"
    assert dgs._preferred_channel(_ki("Send the key", "I'll send it via Outlook")) == "email"


def test_preferred_channel_detects_teams():
    assert dgs._preferred_channel(_ki("Ping them on Teams about the key")) == "teams"


def test_preferred_channel_none_when_unstated_or_ambiguous():
    assert dgs._preferred_channel(_ki("Send the updated roadmap")) is None
    assert dgs._preferred_channel(_ki("email or teams, your call")) is None   # both → ambiguous


def test_classify_honours_email_request_even_when_teams_reachable(monkeypatch):
    # Recipient reachable on BOTH channels; the item asks for email → email_draft.
    monkeypatch.setattr(dgs, "_vault_has", lambda db, tok: True)   # has_aad and has_email
    item = _ki("Email production OpenAI key to recipient")
    assert dgs._classify(item, db=None, recipient_token="S1_PERSON_2")[0] == "email"


def test_classify_defaults_to_teams_when_no_channel_stated(monkeypatch):
    monkeypatch.setattr(dgs, "_vault_has", lambda db, tok: True)
    item = _ki("Send the updated roadmap")
    assert dgs._classify(item, db=None, recipient_token="S1_PERSON_2")[0] == "teams_message"


def test_classify_email_request_falls_back_to_teams_when_no_email(monkeypatch):
    # Asked for email but the recipient has only a Teams (AAD) token → use Teams.
    monkeypatch.setattr(dgs, "_vault_has", lambda db, tok: tok.endswith("_AAD"))
    item = _ki("Email the key to recipient")
    assert dgs._classify(item, db=None, recipient_token="S1_PERSON_2")[0] == "teams_message"


# ── Send-time backstop: never deliver to the configured service account ────────

def test_is_service_account_matches_configured_identity():
    import api.agent as agent
    assert agent._is_service_account(agent.settings.SENDER_IDENTITY)
    assert agent._is_service_account("  CARE.TAKER@AMPERATECH.AI ")   # case/space-insensitive
    assert not agent._is_service_account("requester@example.com")
    assert not agent._is_service_account(None)


def test_email_send_refuses_service_account_recipient(monkeypatch):
    """Even if upstream routing somehow resolved a recipient to the Caretaker account,
    the send path must refuse and audit — never deliver to care.taker@amperatech.ai."""
    import api.agent as agent

    action = SimpleNamespace(id=1)
    payload = {
        "recipient_token": "TOK_EMAIL", "subject": "s", "body": "b",
        "sender_identity": agent.settings.SENDER_IDENTITY,
    }
    monkeypatch.setattr(agent, "_stale_reason", lambda p, db: None)
    # recipient decrypts to the service account address
    monkeypatch.setattr(agent.VaultService, "decrypt",
                        staticmethod(lambda *a, **k: agent.settings.SENDER_IDENTITY))
    sent = {}

    class _FakeEmailSender:
        def __init__(self, upn=None): ...
        def send_mail(self, *a, **k):
            sent["called"] = True

    monkeypatch.setattr(agent, "EmailSender", _FakeEmailSender)
    captured = {}
    monkeypatch.setattr(agent, "_audit_failed",
                        lambda action, db, reason, msg: captured.update(reason=reason) or {"reason": reason})

    agent._execute_email_draft(action, payload, db=None)

    assert captured.get("reason") == "recipient_is_service_account"
    assert "called" not in sent   # nothing was ever sent
