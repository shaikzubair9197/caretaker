"""
Tests for Phase 4 — Intelligent Secure Reference Resolution + Click-to-Reveal
(Follow-up Center plan).

Covers, with NO network (no Ollama/Azure) and an in-memory SQLite DB:
  - descriptor parsing → SecureReference(s) (single / multi-system / generic / none)
  - shared-vocab parity between graph_sync (Phase 2) and secure_store_service (Phase 4)
  - the resolve→decide pipeline: auto-resolve / ambiguous-clarify / no-match-notice
  - the {{SECURE_REF:n}} anti-hallucination verifier
  - draft generation embedding a resolved placeholder — and the SECURITY INVARIANT
    that the plaintext value reaches NEITHER the LLM context NOR the stored payload
  - the click-to-reveal endpoint resolving to the latest active value, audited,
    via a dedicated approved reveal action (the draft itself stays pending)
  - send-time _rehydrate substituting {{SECURE_REF:n}} with the latest active value

In-memory SQLite with StaticPool so the independent vault audit writer (its own
SessionLocal) writes into the SAME DB the test reads.
"""

import json

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.vault_service as vault_service
import services.draft_generation_service as dgs
from api import drafts as drafts_api
from api.agent import _rehydrate
from database.models import (
    AgentAction,
    AuditEvent,
    Base,
    KnowledgeItem,
    MeetingTranscript,
)
from services import secure_store_service as store

_MASTER_KEY = "33" * 32   # 64 hex chars = 32 bytes


# AuditEvent.id is BigInteger — SQLite won't auto-assign it. Batch-safe variant of
# the tests/test_secure_store_service.py shim: a monotonic counter so MULTIPLE audit
# rows flushed together (e.g. the regenerate path) get distinct ids (real Postgres
# autoincrements; this only matters for the in-memory SQLite harness).
_audit_id_seq = {"n": 0}


@event.listens_for(AuditEvent, "before_insert")
def _assign_audit_event_id(mapper, connection, target):
    # Always assign (override any other module's simpler shim that may have fired
    # first) so a multi-row flush gets distinct, monotonically increasing ids
    # regardless of listener registration order across test modules.
    base = connection.execute(select(func.coalesce(func.max(AuditEvent.id), 0))).scalar() or 0
    _audit_id_seq["n"] = max(_audit_id_seq["n"], base) + 1
    target.id = _audit_id_seq["n"]


class _AuditProxy:
    """For the independent audit writer's SessionLocal: route writes into the test
    session WITHOUT committing/closing it (commit→flush) so the caller's in-flight
    transaction is never torn down underneath it."""

    def __init__(self, real):
        self._real = real

    def add(self, obj):
        self._real.add(obj)

    def commit(self):
        self._real.flush()

    def rollback(self):
        pass

    def close(self):
        pass


class _EndpointProxy:
    """For an API endpoint's SessionLocal(): delegate everything to the shared test
    session; commit really commits the in-memory DB; close is a no-op so the
    session stays usable across the test."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr("database.connection.SessionLocal", lambda: _AuditProxy(session))
    monkeypatch.setattr("utils.config.settings.VAULT_MASTER_KEY", _MASTER_KEY)
    monkeypatch.setattr("utils.config.settings.VAULT_KEY_VERSION", 1)
    monkeypatch.setenv("VAULT_MASTER_KEY", _MASTER_KEY)
    vault_service._key_cache.clear()
    try:
        yield session
    finally:
        session.close()


def _seed(db, value, *, system, env=None, ctype="api_key"):
    context = {"environment": env} if env else {}
    return store.upsert_credential(
        value,
        {"credential_type": ctype, "system_name": system, "context": context, "source_type": "teams_chat"},
        db,
    )


class _LLM:
    """Minimal stand-in for LLMService.LLMCallResult."""

    def __init__(self, data):
        self.succeeded = True
        self.data = data
        self.status = "SUCCESS"


# ── Descriptor parsing → SecureReference ─────────────────────────────────────

def test_build_reference_single_system_with_env():
    refs = store.build_secure_references("Send the production OpenAI API key to <S1_PERSON_3>")
    assert refs == [{"credential_type": "api_key", "system_name": "openai", "context": {"environment": "production"}}]


def test_build_reference_multiple_systems():
    refs = store.build_secure_references("share the OpenAI and Azure API keys")
    systems = sorted(r["system_name"] for r in refs)
    assert systems == ["azure", "openai"]
    assert all(r["credential_type"] == "api_key" for r in refs)


def test_build_reference_connection_string_no_system():
    refs = store.build_secure_references("send me the staging connection string")
    assert refs == [{"credential_type": "connection_string", "system_name": None, "context": {"environment": "staging"}}]


def test_build_reference_generic_credential_leaves_type_none():
    # Generic wording must NOT over-constrain credential_type (would trip the hard filter).
    refs = store.build_secure_references("can you send me the credentials")
    assert refs == [{"credential_type": None, "system_name": None, "context": {}}]


def test_build_reference_none_when_no_descriptor():
    assert store.build_secure_references("let's sync about the roadmap tomorrow") == []


def test_shared_vocab_parity_with_graph_sync():
    """Phase 2 sync and Phase 4 resolution MUST infer identically (single source of
    truth) — the credential a value is stored under must equal the one it resolves by."""
    from api.graph_sync import _infer_system_and_context
    sample = "production OpenAI api key"
    assert _infer_system_and_context(sample) == store.infer_system_and_context(sample)


# ── resolve → decide pipeline ────────────────────────────────────────────────

def _item(db, title, *, knowledge_type="action_item", owner="S1_SELF", detail=""):
    it = KnowledgeItem(
        source_id=None,
        knowledge_type=knowledge_type,
        title_masked=title,
        detail_masked=detail,
        owner_token=owner,
        version=1,
        knowledge_key="kkey_1",
        status="open",
        is_active=True,
        user_id=1,
        extra_data={},
    )
    db.add(it)
    db.flush()
    return it


def test_resolve_auto_high_confidence(db):
    _seed(db, "sk-OPENAI-PROD", system="openai", env="production")
    item = _item(db, "Send the production OpenAI API key to <S1_PERSON_3>")
    status, data = dgs._resolve_secure_refs(item, db, is_secret_request=True)
    assert status == "resolved"
    assert data["1"]["credential_key"] == "openai:production:api_key"
    assert "value" not in json.dumps(data).lower() or "sk-openai-prod" not in json.dumps(data).lower()


def test_resolve_ambiguous_triggers_clarification(db):
    _seed(db, "sk-OPENAI-PROD", system="openai", env="production")
    _seed(db, "sk-AZURE-PROD", system="azure", env="production")
    # No system named → both prod api_keys match equally → ambiguous.
    item = _item(db, "please send me the production api key")
    status, data = dgs._resolve_secure_refs(item, db, is_secret_request=True)
    assert status == "clarification"
    assert data["reason"] == "ambiguous_credential"
    assert len(data["candidates"]) == 2
    # candidate cards are masked labels only — never the secret value
    assert "sk-" not in json.dumps(data)


def test_resolve_zero_matches_is_no_matching_notice(db):
    _seed(db, "sk-OPENAI-PROD", system="openai", env="production")
    item = _item(db, "send me the Stripe api key")
    status, data = dgs._resolve_secure_refs(item, db, is_secret_request=True)
    assert status == "clarification"
    assert data["reason"] == "no_matching_credential"
    assert data["unmatched"]


def test_not_a_credential_item_returns_none(db):
    item = _item(db, "follow up with <S1_PERSON_2> about the roadmap")
    assert dgs._resolve_secure_refs(item, db, is_secret_request=False) == (None, None)


# ── {{SECURE_REF:n}} verifier ────────────────────────────────────────────────

def test_verify_secure_refs_accepts_known_token():
    assert dgs._verify_secure_refs({"body": "here: {{SECURE_REF:1}}"}, {"1": {}}) is True


def test_verify_secure_refs_rejects_unknown_token():
    assert dgs._verify_secure_refs({"body": "here: {{SECURE_REF:2}}"}, {"1": {}}) is False


def test_verify_secure_refs_no_tokens_ok():
    assert dgs._verify_secure_refs({"body": "no creds here"}, None) is True


# ── Draft generation: placeholder embedded, value never seen ─────────────────

def test_process_item_embeds_placeholder_never_plaintext(db, monkeypatch):
    secret = "sk-OPENAI-PROD-SECRET-VALUE"
    _seed(db, secret, system="openai", env="production")
    transcript = MeetingTranscript(
        source_id=1,
        external_id="evt-1",
        meeting_id="mtg-1",
        subject_masked="Sync",
        participant_tokens=["S1_PERSON_3"],
        self_token="S1_SELF",
    )
    db.add(transcript)
    db.flush()
    item = _item(db, "Send the production OpenAI API key to <S1_PERSON_3>")

    captured = {}

    def fake_generate_draft(draft_type, masked_context, retrieval_results, source_id=None):
        captured["context"] = masked_context
        return _LLM({"subject": "Your OpenAI key", "body": "As requested:\n{{SECURE_REF:1}}", "citations": []})

    def fake_retrieve(query, db_, user_id=1, **kw):
        return {
            "status": "ok",
            "intent": {"is_secret_request": True},
            "results": [{"value_ref": "vr", "confidence": 0.9, "source_type": "transcript", "source_id": 1}],
            "conflict": False,
        }

    monkeypatch.setattr(dgs.LLMService, "generate_draft", staticmethod(fake_generate_draft))
    monkeypatch.setattr(dgs.knowledge_retrieval_service, "retrieve", fake_retrieve)

    action = dgs.DraftGenerationService._process_item(
        item, ("email", "email_draft", True, False), transcript, db
    )

    assert action is not None
    assert action.payload["secure_refs"]["1"]["credential_key"] == "openai:production:api_key"
    assert "{{SECURE_REF:1}}" in action.payload["body"]
    # SECURITY INVARIANT — value reaches neither the LLM context nor the payload.
    assert secret not in json.dumps(captured["context"])
    assert secret not in json.dumps(action.payload)


# ── Click-to-reveal endpoint ─────────────────────────────────────────────────

def test_reveal_endpoint_resolves_latest_active_and_audits(db, monkeypatch):
    secret = "sk-REVEAL-LATEST"
    _seed(db, secret, system="openai", env="production")
    key = "openai:production:api_key"
    draft = AgentAction(
        action_type="email_draft",
        status="pending",
        user_id=1,
        payload={
            "body": "Here you go: {{SECURE_REF:1}}",
            "secure_refs": {"1": {"credential_key": key, "masked_label": "production openai api_key", "status": "resolved"}},
        },
    )
    db.add(draft)
    db.commit()

    monkeypatch.setattr(drafts_api, "SessionLocal", lambda: _EndpointProxy(db))

    resp = drafts_api.reveal_credentials(draft.id)

    assert resp["revealed"]["1"]["value"] == secret
    assert resp["errors"] == {}
    # dedicated approved reveal action was created and marked executed
    reveal = db.query(AgentAction).filter(AgentAction.id == resp["reveal_action_id"]).first()
    assert reveal.action_type == "credential_reveal" and reveal.status == "executed"
    # one CREDENTIAL_REVEAL audit, and the value is NOT in it
    rev_audits = db.query(AuditEvent).filter(AuditEvent.event_type == "CREDENTIAL_REVEAL").all()
    assert len(rev_audits) == 1
    assert secret not in json.dumps(rev_audits[0].event_data or {})
    # the draft payload is untouched — still masked, no plaintext persisted
    db.refresh(draft)
    assert secret not in json.dumps(draft.payload)


def test_reveal_endpoint_uses_rotated_value_without_regeneration(db, monkeypatch):
    _seed(db, "sk-OLD", system="openai", env="production")
    key = "openai:production:api_key"
    draft = AgentAction(
        action_type="email_draft", status="pending", user_id=1,
        payload={"body": "{{SECURE_REF:1}}", "secure_refs": {"1": {"credential_key": key, "status": "resolved"}}},
    )
    db.add(draft)
    db.commit()
    # rotate AFTER the draft was generated
    _seed(db, "sk-NEW-ROTATED", system="openai", env="production")

    monkeypatch.setattr(drafts_api, "SessionLocal", lambda: _EndpointProxy(db))
    resp = drafts_api.reveal_credentials(draft.id)
    assert resp["revealed"]["1"]["value"] == "sk-NEW-ROTATED"


def test_reveal_endpoint_400_when_no_refs(db, monkeypatch):
    from fastapi import HTTPException
    draft = AgentAction(action_type="email_draft", status="pending", user_id=1, payload={"body": "hi"})
    db.add(draft)
    db.commit()
    monkeypatch.setattr(drafts_api, "SessionLocal", lambda: _EndpointProxy(db))
    with pytest.raises(HTTPException) as exc:
        drafts_api.reveal_credentials(draft.id)
    assert exc.value.status_code == 400


# ── Send-time _rehydrate substitutes the placeholder ─────────────────────────

def test_rehydrate_substitutes_secure_ref_at_send(db):
    secret = "sk-SEND-VALUE"
    _seed(db, secret, system="openai", env="production")
    key = "openai:production:api_key"
    approved = AgentAction(action_type="email_draft", status="approved", user_id=1, payload={})
    db.add(approved)
    db.flush()
    out = _rehydrate(
        "Your key: {{SECURE_REF:1}}", db, approved.id, "send",
        secure_refs={"1": {"credential_key": key}},
    )
    assert out == f"Your key: {secret}"


def test_rehydrate_leaves_unresolved_ref_masked(db):
    approved = AgentAction(action_type="email_draft", status="approved", user_id=1, payload={})
    db.add(approved)
    db.flush()
    # ref present in text but no credential_key mapped → left masked, not crashed
    out = _rehydrate("Key: {{SECURE_REF:1}}", db, approved.id, "send", secure_refs={"1": {}})
    assert out == "Key: {{SECURE_REF:1}}"


# ── R2: strict placeholder-integrity bijection ───────────────────────────────

def test_verify_secure_refs_rejects_duplicate_id():
    assert dgs._verify_secure_refs({"body": "{{SECURE_REF:1}} and {{SECURE_REF:1}}"}, {"1": {}}) is False


def test_verify_secure_refs_rejects_missing_mapping():
    # secure_refs entry with no placeholder in the text → reject (missing mapping)
    assert dgs._verify_secure_refs({"body": "no placeholder here"}, {"1": {}}) is False


def test_verify_secure_refs_rejects_out_of_range_id():
    assert dgs._verify_secure_refs({"body": "{{SECURE_REF:9}}"}, {"1": {}}) is False


def _transcript(db, source_id=1):
    t = MeetingTranscript(
        source_id=source_id, external_id=f"evt-{source_id}", meeting_id=f"mtg-{source_id}",
        subject_masked="Sync", participant_tokens=["S5_PERSON_2"], self_token="S5_SELF",
    )
    db.add(t)
    db.flush()
    return t


def _mock_retrieve_secret():
    def fake_retrieve(query, db_, user_id=1, **kw):
        return {
            "status": "ok",
            "intent": {"is_secret_request": True},
            "results": [{"value_ref": "vr", "confidence": 0.9, "source_type": "transcript", "source_id": 1}],
            "conflict": False,
        }
    return fake_retrieve


def test_process_item_regenerates_then_succeeds(db, monkeypatch):
    _seed(db, "sk-OPENAI", system="openai", env="production")
    transcript = _transcript(db)
    item = _item(db, "Send the production OpenAI API key to <S5_PERSON_2>", owner="S5_SELF")

    # attempt 1: orphan placeholder (invalid) → must regenerate; attempt 2: valid.
    responses = [
        _LLM({"subject": "k", "body": "oops {{SECURE_REF:2}}", "citations": []}),
        _LLM({"subject": "k", "body": "here: {{SECURE_REF:1}}", "citations": []}),
    ]
    calls = {"n": 0}

    def fake_generate_draft(draft_type, masked_context, retrieval_results, source_id=None):
        calls["n"] += 1
        return responses.pop(0)

    monkeypatch.setattr(dgs.LLMService, "generate_draft", staticmethod(fake_generate_draft))
    monkeypatch.setattr(dgs.knowledge_retrieval_service, "retrieve", _mock_retrieve_secret())

    action = dgs.DraftGenerationService._process_item(
        item, ("email", "email_draft", True, False), transcript, db
    )
    assert action is not None
    assert calls["n"] == 2                       # regenerated exactly once
    assert "{{SECURE_REF:1}}" in action.payload["body"]
    regen = db.query(AuditEvent).filter(AuditEvent.event_type == "DRAFT_REGENERATED").all()
    assert len(regen) == 1


def test_process_item_fails_after_max_attempts(db, monkeypatch):
    _seed(db, "sk-OPENAI", system="openai", env="production")
    transcript = _transcript(db)
    item = _item(db, "Send the production OpenAI API key to <S5_PERSON_2>", owner="S5_SELF")

    def always_bad(draft_type, masked_context, retrieval_results, source_id=None):
        return _LLM({"subject": "k", "body": "bad {{SECURE_REF:7}}", "citations": []})

    monkeypatch.setattr(dgs.LLMService, "generate_draft", staticmethod(always_bad))
    monkeypatch.setattr(dgs.knowledge_retrieval_service, "retrieve", _mock_retrieve_secret())

    action = dgs.DraftGenerationService._process_item(
        item, ("email", "email_draft", True, False), transcript, db
    )
    assert action is None
    failed = db.query(AuditEvent).filter(AuditEvent.event_type == "DRAFT_FAILED").all()
    assert any((e.event_data or {}).get("reason") == "unverifiable_secure_ref" for e in failed)


# ── R5: clarification persists the inferred SecureReference ───────────────────

def test_clarification_persists_pending_secure_refs(db):
    _seed(db, "sk-OPENAI", system="openai", env="production")
    _seed(db, "sk-AZURE", system="azure", env="production")
    item = _item(db, "please send me the production api key", owner="S5_SELF")
    status, data = dgs._resolve_secure_refs(item, db, is_secret_request=True)
    assert status == "clarification"
    pending = data["pending_secure_refs"]
    assert pending and pending[0]["owner"] == "S5_SELF"
    assert "credential_type" in pending[0] and "system_name" in pending[0] and "context" in pending[0]


# ── R3: reveal authorization (ownership) ─────────────────────────────────────

def test_reveal_denied_for_non_owner(db, monkeypatch):
    from fastapi import HTTPException
    _seed(db, "sk-SECRET", system="openai", env="production")
    draft = AgentAction(
        action_type="email_draft", status="pending", user_id=999,   # NOT the principal (1)
        payload={"body": "{{SECURE_REF:1}}", "secure_refs": {"1": {"credential_key": "openai:production:api_key"}}},
    )
    db.add(draft)
    db.commit()
    monkeypatch.setattr(drafts_api, "SessionLocal", lambda: _EndpointProxy(db))

    with pytest.raises(HTTPException) as exc:
        drafts_api.reveal_credentials(draft.id)
    assert exc.value.status_code == 403
    # an ACCESS_DENIED audit was written, and NO credential was revealed
    assert db.query(AuditEvent).filter(AuditEvent.event_type == "ACCESS_DENIED").count() == 1
    assert db.query(AuditEvent).filter(AuditEvent.event_type == "CREDENTIAL_REVEAL").count() == 0


# ── R4: rotation consistency — reveal and send use the SAME newest version ────

def test_rotation_consistency_reveal_and_send(db, monkeypatch):
    # 1) draft generated against the OLD value. Caretaker (self=S5_SELF) committed to
    # send the key to the REQUESTER (S5_PERSON_2) — so the draft must route to the
    # requester, never to the Caretaker service account.
    _seed(db, "sk-OLD-VALUE", system="openai", env="production")
    transcript = _transcript(db)   # self_token=S5_SELF, participant_tokens=[S5_PERSON_2]
    item = _item(db, "Send the production OpenAI API key to <S5_PERSON_2>", owner="S5_SELF")
    item.extra_data = {"counterparty": {"kind": "person", "token": "S5_PERSON_2"}}
    db.flush()

    def fake_generate_draft(draft_type, masked_context, retrieval_results, source_id=None):
        return _LLM({"subject": "Your key", "body": "Here: {{SECURE_REF:1}}", "citations": []})

    monkeypatch.setattr(dgs.LLMService, "generate_draft", staticmethod(fake_generate_draft))
    monkeypatch.setattr(dgs.knowledge_retrieval_service, "retrieve", _mock_retrieve_secret())
    recipient_token = dgs._resolve_recipient_token(item, transcript)
    assert recipient_token == "S5_PERSON_2"   # requester, NOT the Caretaker self
    draft = dgs.DraftGenerationService._process_item(
        item, ("email", "email_draft", True, False), transcript, db, recipient_token
    )
    assert draft.payload["secure_refs"]["1"]["credential_key"] == "openai:production:api_key"
    assert draft.payload["recipient_token"] == "S5_PERSON_2_EMAIL"   # never S5_SELF_*

    # recipient token so the send path can decrypt the address (the REQUESTER's)
    from services.vault_service import VaultService
    VaultService.store_tokens({"S5_PERSON_2_EMAIL": "requester@example.com"}, transcript.source_id, db)
    db.commit()

    # 2) credential ROTATES after the draft was generated
    _seed(db, "sk-NEW-ROTATED", system="openai", env="production")

    # 3) REVEAL → newest value, no regeneration
    monkeypatch.setattr(drafts_api, "SessionLocal", lambda: _EndpointProxy(db))
    reveal_resp = drafts_api.reveal_credentials(draft.id)
    revealed_value = reveal_resp["revealed"]["1"]["value"]
    assert revealed_value == "sk-NEW-ROTATED"

    # 4) SEND (approve + execute) with a captured EmailSender → same newest value
    import api.agent as agent
    sent = {}

    class _FakeEmailSender:
        def __init__(self, upn=None):
            pass

        def send_mail(self, to, subject, body):
            sent.update(to=to, subject=subject, body=body)
            return {"id": "msg-1"}

    monkeypatch.setattr(agent, "EmailSender", _FakeEmailSender)
    agent._do_approve(draft, db)

    assert "sk-NEW-ROTATED" in sent["body"]
    assert "sk-OLD-VALUE" not in sent["body"]
    # reveal and send resolved to the EXACT same newest active version
    assert revealed_value in sent["body"]
    # routing invariant: delivered to the requester, NEVER the Caretaker account
    assert sent["to"] == "requester@example.com"
    assert sent["to"] != "care.taker@amperatech.ai"
