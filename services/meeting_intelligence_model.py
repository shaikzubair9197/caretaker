"""
MeetingIntelligence — in-memory domain model for LLM-extracted meeting items.

This model is never persisted directly and has no ORM mapping of its own.
It exists only between LLMService.extract_meeting_intelligence() and
knowledge_persistence_service, which is the sole place that produces
KnowledgeItem rows.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger("services.meeting_intelligence_model")

_COUNTERPARTY_KINDS = {"person", "team", "department", "vendor", "customer", "group"}
_TOKEN_LIKE = re.compile(r"^<?[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*_\d+>?$")


def _normalize_counterparty(raw) -> Optional[dict]:
    """
    Coerce the LLM's `counterparty` into the canonical shape, or None.

    Accepts a dict {token?, name?, kind?} or a bare string. A token-looking string
    (e.g. "<S5_PERSON_3>" or "PERSON_3") becomes a person token (brackets stripped);
    any other string becomes a named entity. Empty/blank → None.
    """
    if not raw:
        return None
    if isinstance(raw, str):
        val = raw.strip()
        if not val:
            return None
        if _TOKEN_LIKE.match(val):
            return {"token": val.strip("<>"), "name": None, "kind": "person"}
        return {"token": None, "name": val, "kind": "group"}
    if isinstance(raw, dict):
        token = (raw.get("token") or "").strip().strip("<>") or None
        name = (raw.get("name") or "").strip() or None
        kind = (raw.get("kind") or "").strip().lower()
        if kind not in _COUNTERPARTY_KINDS:
            kind = "person" if token else "group"
        if not token and not name:
            return None
        return {"token": token, "name": name, "kind": kind}
    return None

VALID_KNOWLEDGE_TYPES = {
    "action_item", "commitment", "deadline", "decision", "risk", "blocker",
    "follow_up", "question", "information_request", "mentioned_document",
    "mentioned_link", "system_reference", "github_pr", "jira_ticket",
    # Plan 2: a reference (never the value) to a credential mentioned/rotated in
    # a meeting. The secret itself lives in the vault; the KnowledgeItem only
    # carries {system_name, credential_kind, vault_token} in extra_data.
    "credential_reference",
}


@dataclass
class MeetingIntelligenceItem:
    """One extracted item — the common envelope shared by every knowledge type."""
    knowledge_type: str
    title:          str
    speaker_token:  Optional[str] = None
    detail:         Optional[str] = None
    owner_token:    Optional[str] = None
    due_hint:       Optional[str] = None
    confidence:     float = 0.0
    extra_data:     dict = field(default_factory=dict)
    counterparty:   Optional[dict] = None
    # the OTHER party a task is directed at (recipient/addressee), when distinct
    # from the owner — a person token or a named entity. Shape:
    # {"token": <token|None>, "name": <str|None>,
    #  "kind": person|team|department|vendor|customer|group}. Drives the
    # Action Item vs Commitment split (Follow-up Center — Phase 3).


@dataclass
class MeetingIntelligence:
    """All items extracted from a single transcript extraction run."""
    items: list[MeetingIntelligenceItem] = field(default_factory=list)

    def of_type(self, knowledge_type: str) -> list[MeetingIntelligenceItem]:
        return [i for i in self.items if i.knowledge_type == knowledge_type]

    @classmethod
    def from_llm_data(cls, raw: Optional[dict]) -> "MeetingIntelligence":
        """
        Parse the raw, schema-validated LLM response dict into typed items.
        Malformed individual items are skipped (logged), not fatal — one bad
        item from the LLM shouldn't drop the whole extraction.
        """
        items: list[MeetingIntelligenceItem] = []
        for raw_item in (raw or {}).get("items", []):
            knowledge_type = raw_item.get("knowledge_type")
            title = raw_item.get("title")
            if knowledge_type not in VALID_KNOWLEDGE_TYPES or not title:
                logger.warning(f"Skipping malformed meeting intelligence item: {raw_item!r}")
                continue
            try:
                confidence = float(raw_item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            items.append(
                MeetingIntelligenceItem(
                    knowledge_type=knowledge_type,
                    title=title,
                    speaker_token=raw_item.get("speaker_token"),
                    detail=raw_item.get("detail"),
                    owner_token=raw_item.get("owner_token"),
                    due_hint=raw_item.get("due_hint"),
                    confidence=max(0.0, min(1.0, confidence)),
                    extra_data=raw_item.get("extra_data") or {},
                    counterparty=_normalize_counterparty(raw_item.get("counterparty")),
                )
            )
        return cls(items=items)
