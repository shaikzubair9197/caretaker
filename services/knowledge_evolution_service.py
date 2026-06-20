"""
KnowledgeEvolutionService — Plan 2 versioning, supersession & identity resolution.

Runs synchronously right after knowledge_persistence_service.persist() (an
appended call in TranscriptIngestionService — Plan 1's ingest flow is NOT
rewritten). For each freshly-persisted KnowledgeItem it:

  1. Resolves the row's `knowledge_key` (deterministic for structured types,
     embedding-similarity + LLM adjudication for free-text — Plan 2 §2).
  2. Looks up the current active version for that key under a row lock
     (SELECT ... FOR UPDATE) — Plan 2 §7 transactional guarantee. The partial
     unique index ux_knowledge_items_active_key is the DB-level backstop.
  3. Decides new chain vs. supersession vs. duplicate-confirmation by comparing
     searchable content, and maintains the validity window + supersession chain.
  4. Populates the (previously unused) `embedding` column — but ONLY when a
     genuinely new version is created. Duplicate confirmations bump confidence
     and leave the existing row's embedding untouched (embedding-regeneration
     gating — Plan 2 §5).

Security (Plan 2 §2, §10):
- credential_reference items embed a FIXED metadata-only template built from
  {system_name, credential_kind, vault_token} — never masked free text, never a
  decrypted secret (which doesn't exist at this layer anyway).
- adjudication sends only masked title/detail to the LLM — same no-raw-PII
  invariant as Plan 1.

Everything here is additive. It never deletes Plan 1 data; the only row it ever
removes is a just-created duplicate it itself produced in this same ingest, so
the documented net effect ("no new row on re-confirmation") holds.
"""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from database import crud
from database.models import KnowledgeItem
from embeddings import embedder, vector_search
from services.llm_service import LLMService
from utils.logger import get_logger
from utils.time_utils import utcnow

logger = get_logger("services.knowledge_evolution")

# Types whose identity is a deterministic function of extra_data — no embedding
# needed to know whether two rows are "the same fact".
_STRUCTURED_KEY_TYPES = {
    "github_pr", "jira_ticket", "system_reference",
    "mentioned_document", "mentioned_link", "credential_reference",
}

# Semantic identity bands for free-text types (Plan 2 §2, §8).
_SEM_HIGH = 0.85   # >= this: confidently the same fact -> chain
_SEM_LOW  = 0.60   # [LOW, HIGH): ambiguous -> LLM adjudication; < LOW: new chain

# Confidence increment applied to an existing row when an unchanged fact is
# re-confirmed by a later meeting.
_CONFIRMATION_BUMP = 0.05


# ── Searchable content / embedding text ──────────────────────────────────────

def _credential_embedding_text(row: KnowledgeItem) -> str:
    """
    Fixed deterministic template for credential_reference rows — Plan 2 §2 hard
    constraint. Contains ONLY metadata identifiers, never title/detail free text
    and never a secret value.
    """
    extra = row.extra_data or {}
    return (
        "credential_reference"
        f"|system={extra.get('system_name', '')}"
        f"|kind={extra.get('credential_kind', '')}"
        f"|token={extra.get('vault_token', '')}"
    )


def _searchable_content(row: KnowledgeItem) -> str:
    """
    The canonical text that defines a row's identity for content comparison and
    embedding. credential_reference uses the metadata-only template; everything
    else uses masked title + detail.
    """
    if row.knowledge_type == "credential_reference":
        return _credential_embedding_text(row)
    title = (row.title_masked or "").strip()
    detail = (row.detail_masked or "").strip()
    return f"{title}\n{detail}".strip()


def _content_identical(existing: KnowledgeItem, incoming: KnowledgeItem) -> bool:
    return _searchable_content(existing) == _searchable_content(incoming)


def _compute_embedding(row: KnowledgeItem) -> Optional[list]:
    """Encode a row's searchable content. Returns None if the embedder is unavailable."""
    return embedder.encode(_searchable_content(row))


# ── Deterministic key derivation (structured types) ──────────────────────────

def _generate_key(row: KnowledgeItem) -> str:
    """A guaranteed-unique key for rows that have no deterministic identity —
    they become their own single-row chain (never falsely merged)."""
    return f"gen:{row.knowledge_type}:{uuid.uuid4().hex}"


def derive_knowledge_key(row: KnowledgeItem) -> Optional[str]:
    """
    Deterministic key for structured types (Plan 2 §2). Returns None for
    free-text types (resolved semantically) and for structured rows missing the
    fields needed to identify them (caller generates a fresh key instead).
    """
    t = row.knowledge_type
    extra = row.extra_data or {}

    if t == "github_pr":
        repo = (extra.get("repo") or "").strip()
        pr = str(extra.get("pr_number") or "").strip()
        if repo or pr:
            return f"github_pr:{repo}:{pr}".lower()
        url = (extra.get("pr_url") or "").strip()
        return f"github_pr:{url}".lower() if url else None

    if t == "jira_ticket":
        key = (extra.get("ticket_key") or "").strip()
        return f"jira_ticket:{key}".lower() if key else None

    if t == "system_reference":
        kind = (extra.get("system_kind") or "").strip()
        return f"system_reference:{kind}".lower() if kind else None

    if t == "mentioned_document":
        url = (extra.get("url") or "").strip()
        if url:
            return f"mentioned_document:{url}".lower()
        kind = (extra.get("document_kind") or "").strip()
        return f"mentioned_document:{kind}".lower() if kind else None

    if t == "mentioned_link":
        url = (extra.get("raw_url") or "").strip()
        return f"mentioned_link:{url}".lower() if url else None

    if t == "credential_reference":
        system = (extra.get("system_name") or "").strip()
        kind = (extra.get("credential_kind") or "").strip()
        if system or kind:
            return f"credential_reference:{system}:{kind}".lower()
        return None

    return None  # free-text type


# ── Write primitives (validity window + supersession chain) ───────────────────

def _activate_new_chain(
    new_row: KnowledgeItem,
    key: str,
    method: str,
    db: Session,
    embedding: Optional[list],
) -> None:
    new_row.knowledge_key = key
    new_row.version = 1
    new_row.is_active = True
    new_row.valid_from = new_row.created_at or utcnow()
    new_row.valid_to = None
    new_row.resolution_method = method
    new_row.embedding = embedding if embedding is not None else _compute_embedding(new_row)
    db.flush()
    logger.info(f"Knowledge new chain key={key} id={new_row.id} method={method}")


def _supersede(
    existing: KnowledgeItem,
    new_row: KnowledgeItem,
    key: str,
    method: str,
    db: Session,
    embedding: Optional[list],
) -> None:
    """Old row → inactive with valid_to; new row → version+1, active. The old
    row is deactivated and flushed BEFORE the new active row is keyed, so the
    partial unique index never sees two active rows for the same key."""
    existing.is_active = False
    existing.valid_to = utcnow()
    existing.superseded_by_id = new_row.id
    existing.resolved_at = existing.resolved_at or utcnow()
    db.flush()

    new_row.knowledge_key = key
    new_row.version = (existing.version or 1) + 1
    new_row.is_active = True
    new_row.valid_from = new_row.created_at or utcnow()
    new_row.valid_to = None
    new_row.resolution_method = method
    new_row.embedding = embedding if embedding is not None else _compute_embedding(new_row)
    db.flush()
    logger.info(
        f"Knowledge supersede key={key} old_id={existing.id}(v{existing.version}) "
        f"-> new_id={new_row.id}(v{new_row.version}) method={method}"
    )


def _confirm_duplicate(existing: KnowledgeItem, new_row: KnowledgeItem, db: Session) -> None:
    """Unchanged fact re-mentioned: bump the existing row's confidence, drop the
    just-created duplicate, leave the existing embedding untouched (Plan 2 §5)."""
    current = float(existing.confidence) if existing.confidence is not None else 0.0
    existing.confidence = round(min(1.0, current + _CONFIRMATION_BUMP), 3)
    db.delete(new_row)
    db.flush()
    logger.info(
        f"Knowledge duplicate confirmation key={existing.knowledge_key} "
        f"id={existing.id} confidence->{existing.confidence} (no new row, embedding unchanged)"
    )


# ── Resolution against a known target key ────────────────────────────────────

def _resolve_against_key(
    new_row: KnowledgeItem,
    key: str,
    method: str,
    db: Session,
    embedding: Optional[list],
) -> str:
    """Given the resolved key, decide new-chain / supersede / duplicate under a
    row lock. Returns the resolution_method actually applied."""
    existing = crud.get_active_knowledge_by_key(
        db, key, user_id=new_row.user_id or 1, for_update=True
    )
    if existing is None or existing.id == new_row.id:
        _activate_new_chain(new_row, key, "new_chain", db, embedding)
        return "new_chain"
    if _content_identical(existing, new_row):
        _confirm_duplicate(existing, new_row, db)
        return "duplicate_confirmation"
    _supersede(existing, new_row, key, method, db, embedding)
    return method


# ── Free-text semantic resolution ────────────────────────────────────────────

def _adjudicate(existing: KnowledgeItem, new_row: KnowledgeItem) -> str:
    """LLM adjudication for the ambiguous similarity band. Falls back to
    UNRELATED (safe: keeps them as separate chains) if the LLM is unavailable."""
    result = LLMService.adjudicate_knowledge_relationship(
        knowledge_type=new_row.knowledge_type,
        existing={
            "title": existing.title_masked,
            "detail": existing.detail_masked,
            "created_at": str(existing.created_at),
        },
        incoming={
            "title": new_row.title_masked,
            "detail": new_row.detail_masked,
            "created_at": str(new_row.created_at),
        },
    )
    if not result.succeeded or not result.data:
        logger.warning(
            f"Adjudication unavailable (status={result.status}) — treating as UNRELATED"
        )
        return "UNRELATED"
    rel = str(result.data.get("relationship", "UNRELATED")).upper()
    if rel not in {"SUPERSEDES", "DUPLICATE", "CONFLICT", "UNRELATED"}:
        rel = "UNRELATED"
    return rel


def _resolve_free_text(new_row: KnowledgeItem, db: Session) -> str:
    query_emb = _compute_embedding(new_row)

    # No embedder → can't resolve identity; start a fresh chain.
    if query_emb is None:
        _activate_new_chain(new_row, _generate_key(new_row), "new_chain", db, None)
        return "new_chain"

    matches = vector_search.search_similar_knowledge(
        db,
        query_emb,
        knowledge_type=new_row.knowledge_type,
        only_active=True,
        limit=5,
        min_score=_SEM_LOW,
        user_id=new_row.user_id or 1,
    )
    # Same-fact resolution is scoped to the same owner (Plan 2 §2).
    candidates = []
    for m in matches:
        if m["id"] == new_row.id:
            continue
        row = crud.get_knowledge_by_id(db, m["id"])
        if row is not None and row.owner_token == new_row.owner_token:
            candidates.append((m["score"], row))

    if not candidates:
        _activate_new_chain(new_row, _generate_key(new_row), "new_chain", db, query_emb)
        return "new_chain"

    score, match_row = candidates[0]

    if score >= _SEM_HIGH:
        return _resolve_against_key(
            new_row, match_row.knowledge_key, "semantic", db, query_emb
        )

    # Ambiguous band -> adjudicate.
    rel = _adjudicate(match_row, new_row)
    if rel in {"SUPERSEDES", "DUPLICATE"}:
        return _resolve_against_key(
            new_row, match_row.knowledge_key, "llm_adjudicated", db, query_emb
        )
    # CONFLICT / UNRELATED -> keep separate. Two active rows can't share a key
    # (unique index), so the new row becomes its own chain; retrieval-time
    # conflict detection (Plan 2 §8) surfaces the disagreement when both match.
    _activate_new_chain(new_row, _generate_key(new_row), "new_chain", db, query_emb)
    return "new_chain"


# ── Public entry point ───────────────────────────────────────────────────────

def resolve_item(new_row: KnowledgeItem, db: Session) -> str:
    """
    Resolve versioning for one freshly-persisted KnowledgeItem. Returns the
    resolution_method applied. Must run inside the caller's open transaction so
    its row locks and the commit are atomic with the rest of ingestion.
    """
    db.flush()  # ensure new_row.id and created_at are populated

    if new_row.knowledge_type in _STRUCTURED_KEY_TYPES:
        key = derive_knowledge_key(new_row)
        if key is None:
            # Structured type but missing identifying fields — own chain, no merge.
            _activate_new_chain(new_row, _generate_key(new_row), "new_chain", db, None)
            return "new_chain"
        return _resolve_against_key(new_row, key, "supersedes", db, embedding=None)

    return _resolve_free_text(new_row, db)


def resolve_created_items(created: list[KnowledgeItem], db: Session) -> list[str]:
    """Resolve a batch of newly-persisted rows in order (later rows can chain
    onto earlier ones from the same ingest). Returns per-row resolution_method.
    One bad row is logged and skipped, never aborting the whole batch."""
    methods: list[str] = []
    for row in created:
        try:
            methods.append(resolve_item(row, db))
        except Exception:
            logger.exception(
                f"Knowledge evolution failed for row id={getattr(row, 'id', None)} "
                f"type={getattr(row, 'knowledge_type', None)} — leaving row as-is"
            )
            methods.append("error")
    return methods
