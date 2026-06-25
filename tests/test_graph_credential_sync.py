"""
Tests for the Phase 2 secure-store credential sync hooked into /graph/sync.

Exercises api.graph_sync._sync_credentials_to_store directly (no Graph network, no
connectors): detection → normalization → upsert into the single SecureStore,
provenance capture, cross-message rotation, and the guarantee that the helper
never mutates the masking inputs and never aborts ingestion on a store failure.
"""

import types

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.vault_service as vault_service
from api.graph_sync import (
    _credential_provenance,
    _infer_system_and_context,
    _ingest_one,
    _sync_credentials_to_store,
)
from database.models import AuditEvent, Base, CredentialVersion, SecureCredential, SourceItem
from services.graph.normalizer import GraphSourceItem
from services.vault_service import VaultService

_MASTER_KEY = "22" * 32


@event.listens_for(AuditEvent, "before_insert")
def _assign_audit_event_id(mapper, connection, target):
    if target.id is None:
        target.id = (connection.execute(select(func.coalesce(func.max(AuditEvent.id), 0))).scalar() or 0) + 1


class _ProxySession:
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


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr("database.connection.SessionLocal", lambda: _ProxySession(session))
    monkeypatch.setattr("utils.config.settings.VAULT_MASTER_KEY", _MASTER_KEY)
    monkeypatch.setattr("utils.config.settings.VAULT_KEY_VERSION", 1)
    monkeypatch.setenv("VAULT_MASTER_KEY", _MASTER_KEY)
    vault_service._key_cache.clear()
    try:
        yield session
    finally:
        session.close()


def _item(source_type="teams_chat", external_id="msg-123", **kw) -> GraphSourceItem:
    return GraphSourceItem(
        source_type=source_type,
        external_id=external_id,
        raw_payload={},
        body_text="(masked elsewhere)",
        subject=kw.get("subject", "credentials"),
        sender=kw.get("sender", "alice@corp.com"),
        sender_name=kw.get("sender_name", "Alice"),
        thread_id=kw.get("thread_id", "thread-9"),
        metadata=kw.get("metadata", {"chat_id": "chat-7", "team_id": "team-1"}),
    )


# ── Detection + normalization + provenance ────────────────────────────────────

def test_api_key_synced_with_inferred_context_and_provenance(db):
    token_map = {"S42_API_KEY_1": "sk-openai-PROD-abc123"}
    redactions = [{"type": "API_KEY", "score": 0.9, "token": "API_KEY_1"}]
    n = _sync_credentials_to_store(
        _item(), 42, token_map, redactions,
        "Here is the OpenAI production API key for the integration.", db,
    )
    db.commit()

    assert n == 1
    cred = db.query(SecureCredential).one()
    assert cred.credential_type == "api_key"
    assert cred.system_name == "openai"
    assert cred.context.get("environment") == "production"

    ver = db.query(CredentialVersion).one()
    assert ver.value_fingerprint == VaultService.fingerprint("sk-openai-PROD-abc123")
    assert bytes(ver.ciphertext) != b"sk-openai-PROD-abc123"   # encrypted at rest

    prov = ver.source_metadata
    assert prov["message_id"] == "msg-123"
    assert prov["chat_id"] == "chat-7" and prov["team_id"] == "team-1"
    assert prov["vault_token"] == "S42_API_KEY_1"
    assert prov["sender"] == "alice@corp.com" and prov["source_type"] == "teams_chat"


def test_helper_does_not_mutate_masking_inputs(db):
    token_map = {"S42_API_KEY_1": "sk-x"}
    snapshot = dict(token_map)
    redactions = [{"type": "API_KEY", "token": "API_KEY_1"}]
    _sync_credentials_to_store(_item(), 42, token_map, redactions, "openai", db)
    db.commit()
    assert token_map == snapshot   # token_map / masked pipeline untouched


def test_non_confidential_redactions_ignored(db):
    token_map = {"S42_PERSON_1": "Alice", "S42_EMAIL_ADDRESS_1": "a@b.com"}
    redactions = [{"type": "PERSON", "token": "PERSON_1"},
                  {"type": "EMAIL_ADDRESS", "token": "EMAIL_ADDRESS_1"}]
    n = _sync_credentials_to_store(_item(), 42, token_map, redactions, "hello team", db)
    db.commit()
    assert n == 0 and db.query(SecureCredential).count() == 0


def test_rotation_across_messages_stable_identity(db):
    # message 1 (source 10) → v1
    _sync_credentials_to_store(
        _item(external_id="m1"), 10, {"S10_API_KEY_1": "v-AAA"},
        [{"type": "API_KEY", "token": "API_KEY_1"}], "openai production", db)
    db.commit()
    # message 2 (source 20), rotated value, SAME system/env → v2 under same identity
    _sync_credentials_to_store(
        _item(external_id="m2"), 20, {"S20_API_KEY_1": "v-BBB"},
        [{"type": "API_KEY", "token": "API_KEY_1"}], "openai production", db)
    db.commit()

    assert db.query(SecureCredential).count() == 1                       # identity stable
    active = db.query(CredentialVersion).filter_by(is_active=True).all()
    assert len(active) == 1 and active[0].version == 2                   # latest active = rotated value


def test_store_failure_never_aborts_ingestion(db, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("vault unavailable")
    monkeypatch.setattr("services.secure_store_service.upsert_credential", _boom)

    n = _sync_credentials_to_store(
        _item(), 42, {"S42_API_KEY_1": "x"},
        [{"type": "API_KEY", "token": "API_KEY_1"}], "openai", db)
    assert n == 0   # logged, swallowed — message ingestion continues


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_infer_system_and_context():
    assert _infer_system_and_context("the OpenAI production key") == ("openai", {"environment": "production"})
    assert _infer_system_and_context("staging azure secret") == ("azure", {"environment": "staging"})
    system, context = _infer_system_and_context("just some opaque value")
    assert system is None and context == {}


def test_provenance_drops_empty_fields():
    item = _item(metadata={})   # no chat/team ids
    prov = _credential_provenance(item, 5, "S5_API_KEY_1")
    assert prov["source_item_id"] == 5
    assert "chat_id" not in prov and "team_id" not in prov


# ── End-to-end through the real _ingest_one (the plan's "Done when") ──────────

def _threat(category="CLEAN"):
    """Minimal ThreatScore stand-in with every attribute _ingest_one reads."""
    return types.SimpleNamespace(
        aggregate=0.9 if category == "QUARANTINE" else 0.0, category=category,
        phishing_score=0.0, bec_score=0.0, credential_risk=0.0, urgency_score=0.0,
        injection_score=0.0, social_eng_score=0.0, link_risk_score=0.0,
        flags=[], recommended_action=None, notes=None,
    )


def _chat_item(external_id, secret):
    return GraphSourceItem(
        source_type="teams_chat",
        external_id=external_id,
        raw_payload={"id": external_id},
        body_text=f"Sharing the OpenAI production credentials. api_key={secret} — use in production.",
        subject="prod creds",
        sender="alice@corp.com",
        sender_name="Alice",
        thread_id="thread-1",
        metadata={"chat_id": "chat-1", "team_id": "team-1"},
    )


def test_ingest_one_creates_credential_and_keeps_text_masked(db, monkeypatch):
    monkeypatch.setattr("api.graph_sync.ThreatEngine.score", lambda *a, **k: _threat("CLEAN"))
    secret = "sk-prod-AAAA1111BBBB2222"

    outcome, err = _ingest_one(_chat_item("m1", secret), db, dry_run=False)
    assert outcome == "ingested" and err is None

    cred = db.query(SecureCredential).one()
    assert cred.credential_type == "api_key" and cred.system_name == "openai"
    assert cred.context.get("environment") == "production"
    assert db.query(CredentialVersion).filter_by(credential_id=cred.id).count() == 1

    # LLM-bound text stays masked — the raw secret never appears in the stored masked_text.
    src = db.query(SourceItem).filter_by(source_type="teams_chat").one()
    assert secret not in (src.masked_text or "")


def test_ingest_one_rotation_creates_v2_and_audit(db, monkeypatch):
    monkeypatch.setattr("api.graph_sync.ThreatEngine.score", lambda *a, **k: _threat("CLEAN"))

    _ingest_one(_chat_item("m1", "sk-prod-AAAA1111BBBB2222"), db, dry_run=False)
    _ingest_one(_chat_item("m2", "sk-prod-CCCC3333DDDD4444"), db, dry_run=False)  # rotated, same identity

    assert db.query(SecureCredential).count() == 1
    active = db.query(CredentialVersion).filter_by(is_active=True).all()
    assert len(active) == 1 and active[0].version == 2
    assert db.query(AuditEvent).filter_by(event_type="CREDENTIAL_ROTATED").count() == 1


def test_ingest_one_quarantine_skips_credential_sync(db, monkeypatch):
    monkeypatch.setattr("api.graph_sync.ThreatEngine.score", lambda *a, **k: _threat("QUARANTINE"))

    outcome, _ = _ingest_one(_chat_item("m1", "sk-prod-AAAA1111BBBB2222"), db, dry_run=False)
    assert outcome == "quarantined"
    # message is still archived, but no credential entered the trusted store
    assert db.query(SourceItem).filter_by(source_type="teams_chat").count() == 1
    assert db.query(SecureCredential).count() == 0


# ── Fix 3: vendor-key detection beats NER (Azure/OpenAI keys) ─────────────────

def test_vendor_prefixed_key_detected_as_api_key():
    """az-/sk- vendor keys must be recognised as API_KEY even though spaCy NER tags
    the embedded brand word ('openai') as ORGANIZATION — the high-confidence
    VENDOR_KEY pattern wins overlap resolution (or the regex fallback catches it)."""
    from services.preprocessing_service import PreprocessingService
    _, token_map, redactions = PreprocessingService.mask_pii_indexed(
        "Azure OpenAI staging key: az-openai-stg-ABCD1234EF"
    )
    assert "API_KEY" in {r["type"] for r in redactions}
    assert any(str(v).startswith("az-openai-stg") for v in token_map.values())


def test_ingest_one_detects_azure_style_key(db, monkeypatch):
    monkeypatch.setattr("api.graph_sync.ThreatEngine.score", lambda *a, **k: _threat("CLEAN"))
    item = GraphSourceItem(
        source_type="teams_chat", external_id="az1", raw_payload={"id": "az1"},
        body_text="Azure OpenAI staging key: az-openai-stg-ABCD1234EF99",
        subject="key", sender="a@corp.com", sender_name="A", thread_id="t",
        metadata={"chat_id": "c1"},
    )
    outcome, err = _ingest_one(item, db, dry_run=False)
    assert outcome == "ingested" and err is None
    assert db.query(SecureCredential).count() == 1   # Azure key is now stored


# ── Fix 2a: forced re-extract backfills credentials on the dedup path ─────────

def _archived(external_id, source_type="teams_chat"):
    si = SourceItem(
        source_type=source_type, raw_text="{}", masked_text="(masked)",
        noise_removed="placeholder", sensitivity_label="CONFIDENTIAL",
        metadata_={"external_id": external_id},
    )
    return si


def test_force_reextract_backfills_credential_on_dup(db, monkeypatch):
    monkeypatch.setattr("api.graph_sync.ThreatEngine.score", lambda *a, **k: _threat("CLEAN"))
    # pre-existing archived message with NO credential in the store (pre-feature ingest)
    db.add(_archived("dup-1"))
    db.commit()
    assert db.query(SecureCredential).count() == 0

    # same external_id ⇒ dedup hit; force_reextract still scans the fresh body
    item = _chat_item("dup-1", "sk-prod-AAAA1111BBBB2222")
    outcome, _ = _ingest_one(item, db, dry_run=False, force_reextract=True)
    assert outcome == "skipped_dup"
    assert db.query(SecureCredential).count() == 1   # backfilled


def test_dup_without_force_does_not_backfill(db, monkeypatch):
    monkeypatch.setattr("api.graph_sync.ThreatEngine.score", lambda *a, **k: _threat("CLEAN"))
    db.add(_archived("dup-2"))
    db.commit()

    item = _chat_item("dup-2", "sk-prod-AAAA1111BBBB2222")
    outcome, _ = _ingest_one(item, db, dry_run=False)   # force_reextract defaults False
    assert outcome == "skipped_dup"
    assert db.query(SecureCredential).count() == 0      # unchanged behaviour preserved


# ── Fix 2b: force_reextract rewinds the connector cursor (full re-fetch) ──────

def test_force_reextract_rewinds_chat_cursor(db, monkeypatch):
    from api import graph_sync
    captured = {}

    class _FakeChat:
        def __init__(self, upn):
            pass

        def fetch_since(self, since):
            captured["since"] = since
            return [], since

    monkeypatch.setattr(graph_sync, "ChatConnector", _FakeChat)
    state = types.SimpleNamespace(delta_token="2026-01-01T00:00:00Z", last_synced_at=None, items_synced=0)
    monkeypatch.setattr(graph_sync, "_get_sync_state", lambda *a, **k: state)

    graph_sync._run_source("chat", "u", db, dry_run=True, force_reextract=True)
    assert captured["since"] is None                    # cursor rewound to re-fetch history

    graph_sync._run_source("chat", "u", db, dry_run=True, force_reextract=False)
    assert captured["since"] == "2026-01-01T00:00:00Z"  # incremental sync unchanged


# ── Fix 1: one-time backfill reprocesses stored SourceItems into the store ────

def test_backfill_one_source_item_populates_store(db, monkeypatch):
    monkeypatch.setattr("api.graph_sync.ThreatEngine.score", lambda *a, **k: _threat("CLEAN"))
    from api.graph_sync import _backfill_one_source_item
    si = SourceItem(
        source_type="teams_chat", raw_text="{}", masked_text="(masked)",
        noise_removed="OpenAI production API key: sk-prod-AAAA1111BBBB2222",
        sensitivity_label="CONFIDENTIAL", metadata_={"external_id": "bf-1", "chat_id": "c9"},
    )
    db.add(si)
    db.flush()

    n = _backfill_one_source_item(si, db)
    db.commit()
    assert n == 1
    cred = db.query(SecureCredential).one()
    assert cred.credential_type == "api_key" and cred.system_name == "openai"
    assert cred.context.get("environment") == "production"
    # idempotent — running again on the same (unchanged) value adds no new version
    assert _backfill_one_source_item(si, db) == 1
    db.commit()
    assert db.query(CredentialVersion).count() == 1
