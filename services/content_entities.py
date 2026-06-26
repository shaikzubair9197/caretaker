"""
content_entities — deterministic, code-registered entity extraction for the
Content Retrieval layer (Document Retrieval plan — Phase 1).

Entities (tickets, repos, projects, customers, technologies, products,
organizations) are a retrieval signal: a query and a document that mention the
same entity should score higher. Extraction is DETERMINISTIC (regex + known
vocabularies) — no LLM, no network — so indexing is cheap, reproducible and
auditable.

Extractors are plugins registered IN CODE via EntityRegistry, mirroring
ui/viewer_registry.py's _register_defaults(). Configuration only TOGGLES an
extractor off by name (CONTENT_ENTITY_EXTRACTORS_DISABLED); it never registers
new types. To add an entity type: write an EntityExtractor and register it in
_register_defaults() — no existing code changes.

Shared by content_processor (per-document extraction at index time) and, in
Phase 2, content_retrieval query building (extract entities from the query text).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from utils.config import settings
from utils.logger import get_logger

logger = get_logger("services.content_entities")


# ── Entity types (open taxonomy; extend via a new extractor) ──────────────────
class EntityType:
    TICKET = "TICKET"
    REPO = "REPO"
    PROJECT = "PROJECT"
    CUSTOMER = "CUSTOMER"
    TECHNOLOGY = "TECHNOLOGY"
    PRODUCT = "PRODUCT"
    ORGANIZATION = "ORGANIZATION"


@dataclass(frozen=True)
class Entity:
    """One extracted entity. `value` is the canonical/display form; `raw` is the
    exact span found (defaults to value). Matching/dedup is case-insensitive via
    key()."""

    entity_type: str
    value: str
    raw: str = ""

    def key(self) -> tuple[str, str]:
        return (self.entity_type, self.value.casefold())

    def as_dict(self) -> dict:
        # Stored on IndexedContent.entities (JSON). Minimal: type + value.
        return {"type": self.entity_type, "value": self.value}


# ── Extractor plugin contract ─────────────────────────────────────────────────
class EntityExtractor(ABC):
    """A deterministic extractor for one entity type. `name` is the registry key
    used by CONTENT_ENTITY_EXTRACTORS_DISABLED (lower-case)."""

    name: str = ""
    entity_type: str = ""

    @abstractmethod
    def extract(self, text: str) -> list[Entity]:  # pragma: no cover - interface
        ...


class RegexEntityExtractor(EntityExtractor):
    """Extracts entities by regular expression. `group` selects the capture group
    that holds the value; `normalize` post-processes the captured value."""

    def __init__(
        self,
        name: str,
        entity_type: str,
        pattern: str,
        group: int = 0,
        flags: int = 0,
        normalize: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.name = name
        self.entity_type = entity_type
        self._regex = re.compile(pattern, flags)
        self._group = group
        self._normalize = normalize

    def extract(self, text: str) -> list[Entity]:
        out: list[Entity] = []
        for match in self._regex.finditer(text):
            raw = match.group(self._group)
            if not raw:
                continue
            value = self._normalize(raw) if self._normalize else raw
            value = value.strip()
            if value:
                out.append(Entity(self.entity_type, value, raw.strip()))
        return out


class VocabularyEntityExtractor(EntityExtractor):
    """Matches a known vocabulary (case-insensitive, whole-word). The vocabulary
    is a built-in default plus an optional config-provided list, read LAZILY at
    extract time so config/test overrides take effect without re-registration."""

    def __init__(
        self,
        name: str,
        entity_type: str,
        builtin: Optional[list[str]] = None,
        vocabulary_provider: Optional[Callable[[], list[str]]] = None,
    ) -> None:
        self.name = name
        self.entity_type = entity_type
        self._builtin = list(builtin or [])
        self._vocabulary_provider = vocabulary_provider

    def _vocabulary(self) -> list[str]:
        terms = list(self._builtin)
        if self._vocabulary_provider:
            try:
                terms.extend(self._vocabulary_provider() or [])
            except Exception as e:  # noqa: BLE001 - never let config break indexing
                logger.warning(f"Vocabulary provider for '{self.name}' failed: {e}")
        # Dedup case-insensitively, keep first spelling, longest-first so a
        # multi-word term wins over a substring of it.
        seen: set[str] = set()
        unique: list[str] = []
        for term in terms:
            term = (term or "").strip()
            cf = term.casefold()
            if term and cf not in seen:
                seen.add(cf)
                unique.append(term)
        unique.sort(key=len, reverse=True)
        return unique

    def extract(self, text: str) -> list[Entity]:
        vocab = self._vocabulary()
        if not vocab:
            return []
        # One alternation regex over escaped terms with word boundaries.
        pattern = r"\b(" + "|".join(re.escape(t) for t in vocab) + r")\b"
        regex = re.compile(pattern, re.IGNORECASE)
        # Map a casefolded match back to its canonical vocab spelling.
        canonical = {t.casefold(): t for t in vocab}
        out: list[Entity] = []
        for match in regex.finditer(text):
            raw = match.group(1)
            value = canonical.get(raw.casefold(), raw)
            out.append(Entity(self.entity_type, value, raw))
        return out


# ── Registry (code-registered; config only toggles) ───────────────────────────
class EntityRegistry:
    _extractors: dict[str, EntityExtractor] = {}

    @classmethod
    def register(cls, extractor: EntityExtractor) -> None:
        if not extractor.name:
            raise ValueError("EntityExtractor.name is required")
        cls._extractors[extractor.name] = extractor

    @classmethod
    def registered_names(cls) -> list[str]:
        return list(cls._extractors.keys())

    @classmethod
    def extractors(cls) -> list[EntityExtractor]:
        """Enabled extractors only — disabled set is read from config each call so
        toggling (and test monkeypatching) takes effect immediately."""
        disabled = set(getattr(settings, "CONTENT_ENTITY_EXTRACTORS_DISABLED", []) or [])
        return [e for name, e in cls._extractors.items() if name not in disabled]

    @classmethod
    def extract_all(cls, text: str) -> list[Entity]:
        """Run every enabled extractor over `text`, deduplicated by (type, value).
        Each extractor is isolated: a failing one is logged and skipped so it can
        never break the indexing pipeline."""
        if not text or not text.strip():
            return []
        seen: set[tuple[str, str]] = set()
        results: list[Entity] = []
        for extractor in cls.extractors():
            try:
                for entity in extractor.extract(text):
                    k = entity.key()
                    if k not in seen:
                        seen.add(k)
                        results.append(entity)
            except Exception as e:  # noqa: BLE001 - one bad extractor must not abort the rest
                logger.warning(f"Entity extractor '{extractor.name}' failed: {e}")
        return results


# ── Default extractors (registered in code — config only toggles) ─────────────
_DEFAULT_TECHNOLOGIES = [
    "python", "java", "javascript", "typescript", "go", "rust",
    "kubernetes", "docker", "postgres", "postgresql", "mysql", "redis",
    "kafka", "rabbitmq", "react", "angular", "vue", "fastapi", "django",
    "flask", "spring", "terraform", "ansible", "aws", "azure", "gcp",
    "graphql", "grpc", "nginx", "elasticsearch", "spark", "airflow",
]

_DEFAULT_PRODUCTS = [
    "jira", "confluence", "salesforce", "slack", "teams", "sharepoint",
    "outlook", "servicenow", "tableau", "snowflake", "databricks", "okta",
    "power bi", "dynamics 365",
]


def _register_defaults() -> None:
    # Tickets: JIRA-style PROJECTKEY-123 (uppercased canonical form).
    EntityRegistry.register(RegexEntityExtractor(
        name="ticket",
        entity_type=EntityType.TICKET,
        pattern=r"\b[A-Z][A-Z0-9]{1,9}-\d+\b",
        normalize=str.upper,
    ))

    # Repositories: owner/repo captured from common git host URLs or an explicit
    # "repo:owner/name" reference (conservative — avoids matching plain paths).
    EntityRegistry.register(RegexEntityExtractor(
        name="repo",
        entity_type=EntityType.REPO,
        pattern=r"(?:github\.com|gitlab\.com|bitbucket\.org)[/:]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?\b",
        group=1,
        flags=re.IGNORECASE,
        normalize=lambda s: s.rstrip("/"),
    ))
    EntityRegistry.register(RegexEntityExtractor(
        name="repo_ref",
        entity_type=EntityType.REPO,
        pattern=r"\brepo:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b",
        group=1,
        flags=re.IGNORECASE,
    ))

    # Organizations: a capitalised name immediately followed by a corporate
    # suffix (Inc/LLC/Corp/Ltd/GmbH/PLC/AG/SA). Heuristic but high-precision.
    EntityRegistry.register(RegexEntityExtractor(
        name="organization",
        entity_type=EntityType.ORGANIZATION,
        pattern=r"\b([A-Z][A-Za-z0-9&.,'\-]*(?:\s+[A-Z][A-Za-z0-9&.,'\-]*){0,3}\s+"
                r"(?:Inc|LLC|L\.L\.C\.|Corp|Corporation|Company|Co|Ltd|Limited|GmbH|PLC|AG|S\.?A\.?))\.?\b",
        group=1,
    ))

    # Vocabulary extractors — config-provided lists (Project/Customer) plus
    # built-in defaults (Technology/Product). Read lazily so config wins.
    EntityRegistry.register(VocabularyEntityExtractor(
        name="project",
        entity_type=EntityType.PROJECT,
        vocabulary_provider=lambda: getattr(settings, "CONTENT_KNOWN_PROJECTS", []),
    ))
    EntityRegistry.register(VocabularyEntityExtractor(
        name="customer",
        entity_type=EntityType.CUSTOMER,
        vocabulary_provider=lambda: getattr(settings, "CONTENT_KNOWN_CUSTOMERS", []),
    ))
    EntityRegistry.register(VocabularyEntityExtractor(
        name="technology",
        entity_type=EntityType.TECHNOLOGY,
        builtin=_DEFAULT_TECHNOLOGIES,
        vocabulary_provider=lambda: getattr(settings, "CONTENT_KNOWN_TECHNOLOGIES", []),
    ))
    EntityRegistry.register(VocabularyEntityExtractor(
        name="product",
        entity_type=EntityType.PRODUCT,
        builtin=_DEFAULT_PRODUCTS,
    ))


_register_defaults()
