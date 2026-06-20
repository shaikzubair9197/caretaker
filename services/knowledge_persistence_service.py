"""
KnowledgePersistenceService — converts a MeetingIntelligence object into
KnowledgeItem rows. This is the ONLY place KnowledgeItem rows get created
from meeting intelligence — extraction (LLMService) never writes to the
database directly.

extra_data is a JSON column, but its contents are not arbitrary: the shape
is fixed per knowledge_type (see _EXTRA_DATA_SCHEMA below) and enforced here
before insert, so nothing ad hoc ends up stored.
"""

import re
from datetime import datetime, timedelta
from typing import Optional

from database.models import KnowledgeItem
from services.meeting_intelligence_model import MeetingIntelligence, MeetingIntelligenceItem
from utils.logger import get_logger

logger = get_logger("services.knowledge_persistence")

# Allowed extra_data keys per knowledge_type. Types not listed here get {} —
# they fit entirely within KnowledgeItem's existing fixed columns.
_EXTRA_DATA_SCHEMA: dict[str, set] = {
    "github_pr":           {"repo", "pr_number", "pr_url", "status_mentioned"},
    "jira_ticket":          {"ticket_key", "status_mentioned"},
    "mentioned_document":  {"document_kind", "url"},
    "mentioned_link":       {"link_kind", "raw_url"},
    "system_reference":    {"system_kind", "related_risk_id", "related_blocker_id"},
    # Plan 2: vault_token references the encrypted secret in vault_tokens; the
    # secret value itself never lives in extra_data (or anywhere outside the
    # vault). system_name + credential_kind form the knowledge_key and the
    # (metadata-only) embedding template — see knowledge_evolution_service.
    "credential_reference": {"vault_token", "system_name", "credential_kind"},
}

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _shape_extra_data(knowledge_type: str, extra_data: dict) -> dict:
    """Keep only the keys defined for this knowledge_type; drop anything else."""
    allowed = _EXTRA_DATA_SCHEMA.get(knowledge_type)
    if not allowed:
        return {}
    return {k: v for k, v in (extra_data or {}).items() if k in allowed}


def _resolve_due_hint(due_hint: Optional[str], reference_dt: Optional[datetime]) -> Optional[datetime]:
    """
    Best-effort resolution of a raw due_hint phrase (e.g. "by Friday", "EOD today")
    into an absolute datetime, relative to the meeting's start time. Returns None
    when it can't be confidently resolved — callers persist due_at as null in that
    case rather than guessing.
    """
    if not due_hint or not reference_dt:
        return None
    text = due_hint.lower()

    if "today" in text:
        return reference_dt.replace(hour=23, minute=59, second=0, microsecond=0)
    if "tomorrow" in text:
        return (reference_dt + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)

    for i, day_name in enumerate(_WEEKDAYS):
        if day_name in text:
            days_ahead = (i - reference_dt.weekday()) % 7
            days_ahead = days_ahead or 7   # "Friday" said on a Friday means *next* Friday
            return (reference_dt + timedelta(days=days_ahead)).replace(
                hour=23, minute=59, second=0, microsecond=0
            )

    return None


def persist(
    intelligence: MeetingIntelligence,
    source_id: int,
    llm_call_log_id: Optional[int],
    meeting_start: Optional[datetime],
    db,
) -> list[KnowledgeItem]:
    """Write one KnowledgeItem row per extracted item. Returns the created rows."""
    created: list[KnowledgeItem] = []

    for item in intelligence.items:
        row = KnowledgeItem(
            source_id=source_id,
            knowledge_type=item.knowledge_type,
            title_masked=item.title,
            detail_masked=item.detail,
            owner_token=item.owner_token,
            due_at=_resolve_due_hint(item.due_hint, meeting_start),
            confidence=round(item.confidence, 3),
            status="open",
            source_type="transcript",
            extra_data=_shape_extra_data(item.knowledge_type, item.extra_data),
            llm_call_log_id=llm_call_log_id,
        )
        db.add(row)
        created.append(row)

    logger.info(f"Persisted {len(created)} KnowledgeItem rows for source_id={source_id}")
    return created
