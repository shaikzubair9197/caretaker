"""
Tests for the presentation-layer display un-masking (Follow-up Center / Meeting Prep).

Verifies the two-view contract:
  • non-secret identity/context tokens (person/org/speaker/email/date) → un-masked
    for the human UI,
  • secrets (api_key/connection_string/aws_key/credit_card/ssn) → STAY masked,
  • {{SECURE_REF:n}} → never touched,
  • unknown/missing/undecryptable tokens → left masked (fail closed).

In-memory SQLite + the real VaultService.store_tokens (AES-GCM) so we exercise the
actual encrypt → store → display-decrypt path. No network, no LLM.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.vault_service as vault_service
from database.models import Base
from services import display_service as ds
from services.vault_service import VaultService

_MASTER_KEY = "22" * 32   # 64 hex chars


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr("utils.config.settings.VAULT_MASTER_KEY", _MASTER_KEY)
    monkeypatch.setattr("utils.config.settings.VAULT_KEY_VERSION", 1)
    monkeypatch.setenv("VAULT_MASTER_KEY", _MASTER_KEY)
    vault_service._key_cache.clear()
    try:
        yield session
    finally:
        session.close()


def _seed(db, mapping, source_id=1):
    VaultService.store_tokens(mapping, source_id, db)
    db.flush()


# ── logical type parsing ──────────────────────────────────────────────────────

def test_logical_type_strips_scope_and_index():
    assert ds._logical_type("S518_ORGANIZATION_1") == "ORGANIZATION"
    assert ds._logical_type("S5_EMAIL_ADDRESS_2") == "EMAIL_ADDRESS"
    assert ds._logical_type("API_KEY_1") == "API_KEY"
    assert ds._logical_type("S9_SPEAKER_3") == "SPEAKER"


# ── un-masking text ───────────────────────────────────────────────────────────

def test_unmasks_non_secret_entities(db):
    _seed(db, {
        "S1_PERSON_1": "John Smith",
        "S1_ORGANIZATION_1": "Ampera Technologies",
        "S1_SPEAKER_2": "Sarah",
        "S1_EMAIL_ADDRESS_1": "sarah@ampera.ai",
    })
    text = "Hi <S1_SPEAKER_2>, <S1_PERSON_1> from <S1_ORGANIZATION_1> (<S1_EMAIL_ADDRESS_1>) asked."
    out = ds.unmask_for_display(text, db)
    assert out == "Hi Sarah, John Smith from Ampera Technologies (sarah@ampera.ai) asked."


def test_secrets_stay_masked(db):
    _seed(db, {
        "S1_PERSON_1": "John Smith",
        "S1_API_KEY_1": "sk-prod-SECRET",
        "S1_CONNECTION_STRING_1": "postgres://u:p@host/db",
        "S1_AWS_KEY_1": "AKIAEXAMPLE",
    })
    text = "Send <S1_PERSON_1> the key <S1_API_KEY_1> and dsn <S1_CONNECTION_STRING_1> / <S1_AWS_KEY_1>"
    out = ds.unmask_for_display(text, db)
    assert "John Smith" in out                       # non-secret resolved
    assert "<S1_API_KEY_1>" in out                   # secret stays masked
    assert "<S1_CONNECTION_STRING_1>" in out
    assert "<S1_AWS_KEY_1>" in out
    assert "sk-prod-SECRET" not in out               # plaintext secret never appears
    assert "postgres://" not in out
    assert "AKIAEXAMPLE" not in out


def test_secure_ref_placeholder_untouched(db):
    _seed(db, {"S1_PERSON_1": "John Smith"})
    text = "Hi <S1_PERSON_1>, here is the key: {{SECURE_REF:1}}"
    out = ds.unmask_for_display(text, db)
    assert out == "Hi John Smith, here is the key: {{SECURE_REF:1}}"
    assert "{{SECURE_REF:1}}" in out


def test_financial_pii_stays_masked(db):
    # Not on the allowlist → must remain masked (privacy), even though not a 'secret'.
    _seed(db, {"S1_CREDIT_CARD_1": "4111111111111111", "S1_US_SSN_1": "123-45-6789"})
    text = "card <S1_CREDIT_CARD_1> ssn <S1_US_SSN_1>"
    out = ds.unmask_for_display(text, db)
    assert out == text
    assert "4111111111111111" not in out


def test_unknown_or_missing_token_left_masked(db):
    _seed(db, {"S1_PERSON_1": "John Smith"})
    # PERSON_9 was never stored; WIDGET is not an allowlisted type
    text = "<S1_PERSON_1> <S1_PERSON_9> <S1_WIDGET_1>"
    out = ds.unmask_for_display(text, db)
    assert out == "John Smith <S1_PERSON_9> <S1_WIDGET_1>"


def test_none_and_plain_text_passthrough(db):
    assert ds.unmask_for_display(None, db) is None
    assert ds.unmask_for_display("no tokens here", db) == "no tokens here"


# ── bare token lists (participant_tokens) ─────────────────────────────────────

def test_display_tokens_resolves_participants_and_keeps_secrets(db):
    _seed(db, {"S1_SPEAKER_1": "Caretaker", "S1_SPEAKER_2": "John", "S1_API_KEY_1": "sk-x"})
    out = ds.display_tokens(["S1_SPEAKER_1", "S1_SPEAKER_2", "S1_API_KEY_1"], db)
    assert out == ["Caretaker", "John", "S1_API_KEY_1"]   # secret token left as-is
