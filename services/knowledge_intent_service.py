"""
KnowledgeIntentService — Plan 2 §1 query classification (thin orchestration).

Turns a raw user request into a structured retrieval intent: how to look it up
(structured vs semantic vs secret), which knowledge_types to search, the salient
search terms, whether it is a secret-value request, and an optional as-of hint.

Security (Plan 2 §10): the query is PII-masked BEFORE it ever reaches the LLM —
the classifier sees only masked text, same no-raw-PII invariant as Plan 1.

Never raises: if the LLM is unavailable or returns garbage, falls back to a
deterministic heuristic so retrieval degrades gracefully instead of 500ing.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from services.llm_service import LLMService
from services.meeting_intelligence_model import VALID_KNOWLEDGE_TYPES
from services.preprocessing_service import PreprocessingService
from utils.logger import get_logger

logger = get_logger("services.knowledge_intent")

# Words that signal the user wants an actual secret VALUE (not just metadata).
_SECRET_RE = re.compile(
    r"\b(password|passwd|api[\s_-]?key|secret|token|credential|connection[\s_-]?string|"
    r"access[\s_-]?key|private[\s_-]?key|bearer)\b",
    re.IGNORECASE,
)

# Follow-up/draft-style phrasing that should stay non-secret unless the query
# explicitly asks for the value of a credential.
_DRAFT_INTENT_RE = re.compile(
    r"\b(email|send|draft|follow[\s-]?up|remind|update|acknowledge|complete|finish|review)\b",
    re.IGNORECASE,
)

# Stronger pattern for a direct request for the actual value of a secret.
_EXPLICIT_SECRET_REQUEST_RE = re.compile(
    r"\b(what(?:'s| is)|give me|send me|share|show me|tell me|need|provide|pull up|"
    r"look up|retrieve|find|locate|email me|message me)\b.{0,60}\b("
    r"password|passwd|api[\s_-]?key|secret|token|credential|connection[\s_-]?string|"
    r"access[\s_-]?key|private[\s_-]?key|bearer)\b",
    re.IGNORECASE,
)


@dataclass
class KnowledgeIntent:
    raw_query:         str
    masked_query:      str
    query_type:        str = "semantic"          # structured | semantic | secret | mixed
    is_secret_request: bool = False
    knowledge_types:   list = field(default_factory=list)
    search_terms:      list = field(default_factory=list)
    as_of_hint:        Optional[str] = None
    classifier_status: str = "FALLBACK"          # SUCCESS | FALLBACK
    llm_call_log_id:   Optional[int] = None


def _heuristic(masked_query: str, raw_query: str) -> dict:
    """Deterministic fallback when the LLM can't classify (Plan 2 graceful degrade)."""
    is_secret = bool(_SECRET_RE.search(raw_query))
    return {
        "query_type": "secret" if is_secret else "semantic",
        "is_secret_request": is_secret,
        "knowledge_types": ["credential_reference"] if is_secret else [],
        "search_terms": [],
        "as_of_hint": None,
    }


def _sanitize_types(types) -> list:
    if not isinstance(types, list):
        return []
    return [t for t in types if t in VALID_KNOWLEDGE_TYPES]


def classify(query: str) -> KnowledgeIntent:
    """Classify a retrieval query into a KnowledgeIntent. Always returns a value."""
    raw_query = query or ""
    masked_query, redactions = PreprocessingService.mask_pii(raw_query)
    entity_types = sorted({r.get("type") for r in (redactions or []) if r.get("type")})

    result = LLMService.classify_knowledge_query(masked_query, entity_types=entity_types)

    if result.succeeded and result.data:
        data = result.data
        status = "SUCCESS"
        log_id = result.call_log_id
    else:
        data = _heuristic(masked_query, raw_query)
        status = "FALLBACK"
        log_id = result.call_log_id
        logger.info(f"Knowledge query classification fell back to heuristic (status={result.status})")

    query_type = data.get("query_type") or "semantic"
    if query_type not in {"structured", "semantic", "secret", "mixed"}:
        query_type = "semantic"

    # Belt-and-suspenders: a keyword secret request always wins, even if the LLM
    # missed it — we must never treat a secret query as a normal one.
    is_secret = bool(data.get("is_secret_request")) or bool(_SECRET_RE.search(raw_query))
    if not _EXPLICIT_SECRET_REQUEST_RE.search(raw_query) and _DRAFT_INTENT_RE.search(raw_query):
        # Draft-generation queries often mention "send", "email", or "follow up"
        # while looking up a normal work item. Don't let that wording route us to
        # secret-only retrieval unless the user explicitly asked for the value.
        is_secret = False
        if query_type == "secret":
            query_type = "semantic"
    if is_secret and query_type not in {"secret", "mixed"}:
        query_type = "secret"

    return KnowledgeIntent(
        raw_query=raw_query,
        masked_query=masked_query,
        query_type=query_type,
        is_secret_request=is_secret,
        knowledge_types=_sanitize_types(data.get("knowledge_types")),
        search_terms=[str(t) for t in (data.get("search_terms") or []) if isinstance(t, (str, int))],
        as_of_hint=data.get("as_of_hint"),
        classifier_status=status,
        llm_call_log_id=log_id,
    )
