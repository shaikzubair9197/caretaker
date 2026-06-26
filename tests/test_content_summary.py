"""
Tests for content_summary — masked LLM summarization (Document Retrieval
follow-on). In-memory SQLite + tmp text cache. The LLM and masker are patched so
the test is deterministic and asserts the key SECURITY invariant: the document
text is masked BEFORE the LLM is called — the model never receives raw text.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, IndexedContent
from services import content_summary
from services.content_processor import text_cache_path
from utils.config import settings


class _FakeResult:
    def __init__(self, succeeded, data=None, status="SUCCESS", reason=""):
        self.succeeded = succeeded
        self.data = data
        self.status = status
        self.reason = reason


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_TEXT_CACHE_DIR", str(tmp_path / "cache"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed(db, content_hash="h1", text="hello", served=True):
    if content_hash and text is not None:
        p = text_cache_path(content_hash)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    row = IndexedContent(
        source_type="LOCAL", source_identifier="/docs/a.docx",
        source_metadata={"root": "/docs", "relative_path": "a.docx"},
        filename="a.docx", folder="docs", extension=".docx",
        content_hash=content_hash, is_active=served,
        index_status="INDEXED" if served else "PENDING", version=1,
    )
    db.add(row)
    db.flush()
    return row


def test_summary_masks_for_llm_then_unmasks_for_user(db, monkeypatch):
    raw = "Contact alice@example.com — the api key is sk-SECRET-VALUE-123."
    row = _seed(db, text=raw)

    captured = {}

    def fake_mask_indexed(text):
        captured["mask_input"] = text
        masked = "Reach <EMAIL_ADDRESS_1> using key <API_KEY_1>."
        token_map = {"EMAIL_ADDRESS_1": "alice@example.com", "API_KEY_1": "sk-SECRET-VALUE-123"}
        redactions = [{"type": "EMAIL_ADDRESS", "token": "EMAIL_ADDRESS_1"},
                      {"type": "API_KEY", "token": "API_KEY_1"}]
        return masked, token_map, redactions

    def fake_llm(masked_text):
        captured["llm_input"] = masked_text
        # LLM preserves the tokens, as instructed.
        return _FakeResult(True, {"summary": "Reach <EMAIL_ADDRESS_1> using key <API_KEY_1>."})

    monkeypatch.setattr("services.content_summary.PreprocessingService.mask_pii_indexed", staticmethod(fake_mask_indexed))
    monkeypatch.setattr("services.content_summary.LLMService.summarize_document", staticmethod(fake_llm))
    monkeypatch.setattr("services.content_summary._audit_unmask", lambda *a, **k: None)

    result = content_summary.summarize(db, row.id)

    assert result["status"] == "ok"
    assert result["masked_from_llm"] is True
    assert result["redaction_count"] == 2
    # The LLM received only the MASKED text — never the raw secret/email.
    assert "alice@example.com" not in captured["llm_input"]
    assert "sk-SECRET-VALUE-123" not in captured["llm_input"]
    # The user-facing summary has the real values RESTORED (unmasked).
    assert "alice@example.com" in result["summary"]
    assert "sk-SECRET-VALUE-123" in result["summary"]
    assert "<EMAIL_ADDRESS_1>" not in result["summary"]


def test_summary_empty_document_skips_llm(db, monkeypatch):
    row = _seed(db, text="   ")
    called = {"llm": False}
    monkeypatch.setattr("services.content_summary.LLMService.summarize_document",
                        staticmethod(lambda t: called.__setitem__("llm", True) or _FakeResult(True, {"summary": "x"})))
    result = content_summary.summarize(db, row.id)
    assert result["status"] == "empty"
    assert called["llm"] is False        # never call the LLM with nothing


def test_summary_is_cached_and_refresh_regenerates(db, monkeypatch):
    row = _seed(db, content_hash="hcache", text="some real document content")
    calls = {"n": 0}

    def fake_llm(masked_text):
        calls["n"] += 1
        return _FakeResult(True, {"summary": "OVERVIEW\n- point"})

    monkeypatch.setattr("services.content_summary.PreprocessingService.mask_pii_indexed",
                        staticmethod(lambda t: (t, {}, [])))
    monkeypatch.setattr("services.content_summary.LLMService.summarize_document", staticmethod(fake_llm))

    r1 = content_summary.summarize(db, row.id)
    r2 = content_summary.summarize(db, row.id)
    assert r1["status"] == "ok" and r2["status"] == "ok"
    assert r1.get("cached") is False
    assert r2.get("cached") is True
    assert calls["n"] == 1                    # LLM called once; second served from cache

    r3 = content_summary.summarize(db, row.id, refresh=True)
    assert r3.get("cached") is False
    assert calls["n"] == 2                    # refresh bypasses the cache


def test_summary_not_found(db):
    assert content_summary.summarize(db, 9999)["status"] == "not_found"


def test_summary_not_served(db):
    row = _seed(db, served=False)         # PENDING, not served
    assert content_summary.summarize(db, row.id)["status"] == "not_found"


def test_summary_llm_error_is_graceful(db, monkeypatch):
    row = _seed(db, text="real content here")
    monkeypatch.setattr("services.content_summary.PreprocessingService.mask_pii_indexed",
                        staticmethod(lambda t: (t, {}, [])))
    monkeypatch.setattr("services.content_summary.LLMService.summarize_document",
                        staticmethod(lambda t: _FakeResult(False, None, status="TIMEOUT", reason="LLM timed out")))
    result = content_summary.summarize(db, row.id)
    assert result["status"] == "llm_error"
    assert result["llm_status"] == "TIMEOUT"
    assert "timed out" in result["reason"]
