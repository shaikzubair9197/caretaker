"""
Tests for content_processor — pure document processing (Document Retrieval plan —
Phase 1). The extracted text is cached to disk (a tmp dir here), never to a DB.
The embedder is monkeypatched so tests are deterministic and never load the model.

Covers: txt/markdown extraction, keyword + entity extraction, embedding capture,
the oversized short-circuit (no bytes read), unsupported/empty inputs, the
embeddings-unavailable fallback, and deterministic content hashing.
"""

import hashlib

import pytest

from services.content_processor import DocumentProcessor, ProcessingError, text_cache_path
from services.content_providers import IndexedContentCandidate
from utils.config import settings


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_TEXT_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache"


@pytest.fixture()
def fixed_embedding(monkeypatch):
    vec = [0.1, 0.2, 0.3]
    monkeypatch.setattr("services.content_processor.encode", lambda text: list(vec))
    return vec


def _candidate(data: bytes, ext=".txt", size=None, reader=None):
    return IndexedContentCandidate(
        source_type="LOCAL",
        source_identifier="/docs/x" + ext,
        source_metadata={"root": "/docs", "relative_path": "x" + ext},
        root_label="docs",
        filename="x" + ext,
        folder="docs",
        folder_path="",
        extension=ext,
        size_bytes=size if size is not None else len(data),
        modified_at=None,
        _reader=reader or (lambda: data),
    )


def test_txt_extraction_keywords_entities_embedding(fixed_embedding, cache_dir):
    data = b"Kubernetes deployment plan for PROJ-123 at Acme Corp"
    result = DocumentProcessor().process(_candidate(data, ".txt"))

    assert result.content_hash == hashlib.sha256(data).hexdigest()
    assert result.text_cache_path == f"{result.content_hash}.txt"
    # Cache file written with the exact extracted text.
    cached = (cache_dir / result.text_cache_path).read_text()
    assert "Kubernetes deployment plan" in cached
    # Keywords + entities.
    assert "kubernetes" in result.keywords
    assert "deployment" in result.keywords
    types = {(e["type"], e["value"]) for e in result.entities}
    assert ("TICKET", "PROJ-123") in types
    assert ("TECHNOLOGY", "kubernetes") in types
    # Embedding captured from the (monkeypatched) embedder.
    assert result.embedding == fixed_embedding
    assert result.embedding_dimension == 3
    assert result.embedding_model == "all-MiniLM-L6-v2"
    assert result.embedded_at is not None


def test_markdown_tags_stripped(fixed_embedding):
    data = b"# Title\n\nDiscussion about **Redis** and Kafka"
    result = DocumentProcessor().process(_candidate(data, ".md"))
    assert "redis" in result.keywords
    assert "kafka" in result.keywords


def test_oversized_short_circuits_without_reading(fixed_embedding):
    def _boom():
        raise AssertionError("bytes should not be read for an oversized file")

    candidate = _candidate(b"x", ".txt", size=50 * 1024 * 1024, reader=_boom)
    proc = DocumentProcessor(max_file_mb=1)
    with pytest.raises(ProcessingError):
        proc.process(candidate)


def test_unsupported_extension_raises(fixed_embedding):
    with pytest.raises(ProcessingError):
        DocumentProcessor().process(_candidate(b"data", ".bin"))


def test_empty_text_indexes_metadata_only(fixed_embedding, cache_dir):
    result = DocumentProcessor().process(_candidate(b"", ".txt"))
    assert result.char_count == 0
    assert result.keywords == []
    assert result.entities == []
    assert result.embedding is None          # nothing to embed
    assert (cache_dir / result.text_cache_path).exists()


def test_embeddings_unavailable_fallback(monkeypatch, cache_dir):
    monkeypatch.setattr("services.content_processor.encode", lambda text: None)
    result = DocumentProcessor().process(_candidate(b"Kubernetes notes", ".txt"))
    assert result.embedding is None
    assert result.embedding_model is None
    assert result.embedding_dimension is None
    # Still a valid result with keywords (serves on non-semantic signals).
    assert "kubernetes" in result.keywords


def test_text_cache_path_helper(cache_dir):
    assert text_cache_path("abc123").name == "abc123.txt"
    assert text_cache_path("abc123") == cache_dir / "abc123.txt"
