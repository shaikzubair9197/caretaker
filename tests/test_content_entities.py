"""
Tests for content_entities — deterministic, code-registered entity extraction
(Document Retrieval plan — Phase 1). No DB, no LLM, no network.

Covers: ticket/repo/organization regex extraction, built-in technology/product
vocabularies, config-driven project vocabulary, the disabled-by-config toggle,
dedup, empty input, and per-extractor failure isolation.
"""

import pytest

from services.content_entities import (
    Entity,
    EntityExtractor,
    EntityRegistry,
    EntityType,
)
from utils.config import settings


def _values(entities, entity_type):
    return {e.value for e in entities if e.entity_type == entity_type}


def test_default_extractors_registered():
    names = EntityRegistry.registered_names()
    for expected in ("ticket", "repo", "organization", "project", "customer", "technology", "product"):
        assert expected in names


def test_ticket_extraction():
    ents = EntityRegistry.extract_all("Please fix PROJ-123 and also ABC9-42 today")
    assert "PROJ-123" in _values(ents, EntityType.TICKET)
    assert "ABC9-42" in _values(ents, EntityType.TICKET)


def test_repo_extraction_from_url():
    ents = EntityRegistry.extract_all("Code lives at https://github.com/acme/widget.git")
    assert "acme/widget" in _values(ents, EntityType.REPO)


def test_technology_builtin_vocabulary_is_canonicalised():
    ents = EntityRegistry.extract_all("We run Kubernetes with Postgres in production")
    techs = _values(ents, EntityType.TECHNOLOGY)
    assert "kubernetes" in techs   # canonical (built-in) spelling, not the input casing
    assert "postgres" in techs


def test_product_builtin_vocabulary():
    ents = EntityRegistry.extract_all("Tracked in Jira and discussed in Teams")
    products = _values(ents, EntityType.PRODUCT)
    assert "jira" in products
    assert "teams" in products


def test_organization_suffix_extraction():
    ents = EntityRegistry.extract_all("Acme Corp announced a partnership")
    orgs = _values(ents, EntityType.ORGANIZATION)
    assert any(o.startswith("Acme") for o in orgs)


def test_project_vocabulary_from_config(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_KNOWN_PROJECTS", ["Apollo", "Borealis"])
    ents = EntityRegistry.extract_all("Status update on Apollo and the new effort")
    assert "Apollo" in _values(ents, EntityType.PROJECT)
    assert "Borealis" not in _values(ents, EntityType.PROJECT)


def test_disabled_extractor_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "CONTENT_ENTITY_EXTRACTORS_DISABLED", ["technology"])
    ents = EntityRegistry.extract_all("We run Kubernetes here")
    assert _values(ents, EntityType.TECHNOLOGY) == set()


def test_dedup_same_entity_once():
    ents = EntityRegistry.extract_all("Kubernetes Kubernetes KUBERNETES")
    techs = [e for e in ents if e.entity_type == EntityType.TECHNOLOGY and e.value == "kubernetes"]
    assert len(techs) == 1


def test_empty_text_returns_empty():
    assert EntityRegistry.extract_all("") == []
    assert EntityRegistry.extract_all("   ") == []


def test_failing_extractor_is_isolated():
    class _Boom(EntityExtractor):
        name = "boom"
        entity_type = "BOOM"

        def extract(self, text):
            raise RuntimeError("boom")

    EntityRegistry.register(_Boom())
    try:
        ents = EntityRegistry.extract_all("Fix PROJ-7 with Kubernetes")
        # The bad extractor must not abort the rest.
        assert "PROJ-7" in _values(ents, EntityType.TICKET)
        assert "kubernetes" in _values(ents, EntityType.TECHNOLOGY)
    finally:
        EntityRegistry._extractors.pop("boom", None)


def test_entity_key_and_serialisation():
    e = Entity(EntityType.TECHNOLOGY, "Kubernetes", "kubernetes")
    assert e.key() == (EntityType.TECHNOLOGY, "kubernetes")
    assert e.as_dict() == {"type": EntityType.TECHNOLOGY, "value": "Kubernetes"}
