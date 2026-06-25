"""
Tests for secure_store_service (Follow-up Center plan — Phase 1).

In-memory SQLite (StaticPool) so the independent vault audit writer — which opens
its own SessionLocal — writes into the SAME DB the test reads. No network, no
Ollama/Azure. Covers: new chain, duplicate (no-op), rotation, structured
resolution (auto / ambiguous / none), the decrypt approval gate + latest-active,
and the no-plaintext-at-rest invariant.
"""

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.vault_service as vault_service
from database.models import AgentAction, AuditEvent, Base, CredentialVersion, SecureCredential
from services import secure_store_service as store

_MASTER_KEY = "11" * 32   # 64 hex chars = 32 bytes


# AuditEvent.id is BigInteger — SQLite won't auto-assign it (same workaround as
# tests/test_knowledge_retrieval.py).
@event.listens_for(AuditEvent, "before_insert")
def _assign_audit_event_id(mapper, connection, target):
    if target.id is None:
        target.id = (connection.execute(select(func.coalesce(func.max(AuditEvent.id), 0))).scalar() or 0) + 1


class _ProxySession:
    """The vault audit writer opens its own SessionLocal — route it into the
    test's in-memory DB. commit()→flush(); close() is a noop so the test session
    stays open."""

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


def _approved_action(db) -> AgentAction:
    a = AgentAction(action_type="email_draft", payload={}, status="approved", user_id=1)
    db.add(a)
    db.flush()
    return a


def _meta(**kw) -> dict:
    base = {"credential_type": "api_key", "system_name": "openai",
            "context": {"environment": "production"}, "source_type": "teams_chat", "source_id": None}
    base.update(kw)
    return base


# ── Write path ────────────────────────────────────────────────────────────────

def test_new_value_creates_active_v1(db):
    ver = store.upsert_credential("sk-openai-AAA111", _meta(), db)
    db.commit()

    assert ver.version == 1 and ver.is_active is True
    key = store.derive_credential_key("api_key", "openai", {"environment": "production"})
    cred = db.query(SecureCredential).filter_by(credential_key=key).one()
    assert cred.active_version_id == ver.id
    assert db.query(CredentialVersion).filter_by(credential_id=cred.id).count() == 1
    assert db.query(AuditEvent).filter_by(event_type="CREDENTIAL_STORED").count() == 1


def test_same_value_is_noop(db):
    v1 = store.upsert_credential("sk-openai-AAA111", _meta(), db)
    db.commit()
    v2 = store.upsert_credential("sk-openai-AAA111", _meta(), db)
    db.commit()

    assert v2.id == v1.id and v2.version == 1
    cred = db.query(SecureCredential).one()
    assert db.query(CredentialVersion).filter_by(credential_id=cred.id).count() == 1
    assert db.query(AuditEvent).filter_by(event_type="CREDENTIAL_ROTATED").count() == 0


def test_rotation_creates_v2_and_one_active(db):
    v1 = store.upsert_credential("sk-openai-AAA111", _meta(), db)
    db.commit()
    v2 = store.upsert_credential("sk-openai-BBB222", _meta(), db)   # rotated value, same identity
    db.commit()

    cred = db.query(SecureCredential).one()
    assert v2.version == 2 and v2.is_active is True
    db.refresh(v1)
    assert v1.is_active is False and v1.valid_to is not None
    active = db.query(CredentialVersion).filter_by(credential_id=cred.id, is_active=True).all()
    assert len(active) == 1 and active[0].id == v2.id
    assert cred.active_version_id == v2.id
    assert db.query(AuditEvent).filter_by(event_type="CREDENTIAL_ROTATED").count() == 1


def test_identity_is_metadata_only_and_stable_across_rotation(db):
    # derive_credential_key takes only metadata — it cannot even see a value, and
    # the same metadata always yields the same key regardless of the value stored.
    meta = {"environment": "production"}
    assert store.derive_credential_key("api_key", "openai", meta) == \
           store.derive_credential_key("api_key", "openai", meta)

    v1 = store.upsert_credential("sk-openai-AAA111", _meta(), db)
    db.commit()
    cred_id, key_v1, fp_v1 = (db.query(SecureCredential).one().id, None, v1.value_fingerprint)
    key_v1 = db.query(SecureCredential).one().credential_key

    v2 = store.upsert_credential("sk-openai-BBB222", _meta(), db)  # rotate value, same metadata
    db.commit()

    # exactly one identity row, same id + same key after rotation
    assert db.query(SecureCredential).count() == 1
    cred = db.query(SecureCredential).one()
    assert cred.id == cred_id and cred.credential_key == key_v1
    # only the version + fingerprint changed
    assert v2.version == 2 and v2.value_fingerprint != fp_v1


def test_no_plaintext_persisted(db):
    secret = "sk-prod-SUPERSECRET-9f3a2b1c8d7e"
    ver = store.upsert_credential(secret, _meta(), db)
    db.commit()

    cred = db.query(SecureCredential).one()
    assert isinstance(ver.ciphertext, (bytes, bytearray)) and bytes(ver.ciphertext) != secret.encode()
    assert secret not in bytes(ver.ciphertext).decode("latin-1")
    assert secret not in (ver.value_fingerprint or "")
    # nothing in the identity row carries the value either
    for field in (cred.credential_key, cred.system_name, str(cred.context), str(ver.source_metadata)):
        assert secret not in field


# ── Read path: structured resolution ────────────────────────────────────────

def test_resolve_auto_high_confidence_single_match(db):
    store.upsert_credential("sk-openai-AAA111", _meta(), db)
    db.commit()

    res = store.resolve_secure_reference(
        {"credential_type": "api_key", "system_name": "openai", "context": {"environment": "production"}}, db)
    assert len(res["matches"]) == 1
    assert res["confidence"] >= store.DEFAULT_RESOLUTION_THRESHOLD


def test_resolve_ambiguous_multiple_matches_low_confidence(db):
    store.upsert_credential("sk-openai-AAA111", _meta(system_name="openai"), db)
    store.upsert_credential("az-key-BBB222", _meta(system_name="azure"), db)
    db.commit()

    res = store.resolve_secure_reference({"credential_type": "api_key"}, db)   # no system → ambiguous
    assert len(res["matches"]) == 2
    assert res["confidence"] < store.DEFAULT_RESOLUTION_THRESHOLD


def test_resolve_zero_matches(db):
    store.upsert_credential("sk-openai-AAA111", _meta(), db)
    db.commit()

    res = store.resolve_secure_reference({"credential_type": "api_key", "system_name": "stripe"}, db)
    assert res["matches"] == [] and res["confidence"] == 0.0


# ── Decrypt: gate + latest-active ────────────────────────────────────────────

def test_decrypt_returns_latest_active_value(db):
    key = store.derive_credential_key("api_key", "openai", {"environment": "production"})
    store.upsert_credential("sk-openai-AAA111", _meta(), db)
    store.upsert_credential("sk-openai-BBB222", _meta(), db)   # rotate
    action = _approved_action(db)
    db.commit()

    out = store.decrypt_credential(key, "send approved draft", "system", action.id, db)
    assert out == "sk-openai-BBB222"   # latest active, never the historical v1
    assert db.query(AuditEvent).filter_by(event_type="CREDENTIAL_REVEAL", outcome="SUCCESS").count() == 1


def test_decrypt_requires_approved_action(db):
    key = store.derive_credential_key("api_key", "openai", {"environment": "production"})
    store.upsert_credential("sk-openai-AAA111", _meta(), db)
    pending = AgentAction(action_type="email_draft", payload={}, status="pending", user_id=1)
    db.add(pending)
    db.commit()

    with pytest.raises(PermissionError):
        store.decrypt_credential(key, "trying", "system", pending.id, db)


def test_decrypt_requires_justification(db):
    key = store.derive_credential_key("api_key", "openai", {"environment": "production"})
    store.upsert_credential("sk-openai-AAA111", _meta(), db)
    action = _approved_action(db)
    db.commit()

    with pytest.raises(ValueError):
        store.decrypt_credential(key, "  ", "system", action.id, db)
