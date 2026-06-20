"""
KnowledgeRetrievalService — Plan 2 §1 retrieval pipeline (thin orchestration).

    classify intent (knowledge_intent_service)
      -> structured retrieval  (crud.list_knowledge_by_filters / get_active_by_key)
      -> semantic retrieval    (vector_search.search_similar_knowledge)
      -> candidate merge (union + de-dup by id)
      -> ranking (confidence model §6, then SOURCE PRECEDENCE tie-break §1)
      -> confidence floor (§6)
      -> version resolution (active-only, or as-of window §7)
      -> conflict detection (§8)
      -> Validated Knowledge Result (expanded schema §1)
      -> one AuditEvent(event_type="RETRIEVAL") per call (§1)

Security (Plan 2 §10):
- Secrets are NEVER decrypted here. Secret requests and credential_reference
  rows return only a vault_token *reference*; decryption stays behind
  VaultService.decrypt() + an approved agent_action_id.
- Only masked text / references flow through scoring and ranking.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from database import crud
from database.models import KnowledgeItem
from embeddings import embedder
from embeddings.vector_search import _cosine_similarity, search_similar_knowledge
from services import knowledge_intent_service
from services.knowledge_evolution_service import _SEM_HIGH, _SEM_LOW, _adjudicate
from utils.logger import get_logger
from utils.time_utils import utcnow

logger = get_logger("services.knowledge_retrieval")

# Tie-break order when two candidates are within CONFIDENCE_EPSILON of each
# other (Plan 2 §1). Higher wins. Forward-looking: Plan 1 only writes
# "transcript" today, but Graph-sourced facts arrive in a later phase.
SOURCE_PRECEDENCE = {
    "transcript_verified": 100,
    "transcript":           80,
    "manual":               70,
    "email":                50,
    "teams_chat":           40,
}

CONFIDENCE_FLOOR   = 0.5    # below this, return "insufficient confidence" (§6)
CONFIDENCE_EPSILON = 0.05   # ties within this band fall through to source precedence (§1)
_RECENCY_HALF_LIFE_DAYS = 30.0

# Recency decay applies to volatile facts; immutable facts keep recency 1.0 (§6).
_VOLATILE_TYPES  = {"credential_reference", "deadline"}
_IMMUTABLE_TYPES = {"decision"}


@dataclass
class KnowledgeResult:
    """Validated Knowledge Result — Plan 2 §1 expanded schema."""
    knowledge_id:      int
    value_ref:         str            # masked text OR vault_token reference — never a secret
    knowledge_type:    str
    knowledge_key:     Optional[str]
    version:           Optional[int]
    confidence:        float
    valid_from:        Optional[str]
    valid_to:          Optional[str]
    source_type:       Optional[str]  # for downstream citation (Plan 3)
    source_id:         Optional[int]  # for downstream citation (Plan 3)
    conflict_flag:     bool
    resolution_method: Optional[str]


@dataclass
class _Candidate:
    row:            KnowledgeItem
    semantic_score: Optional[float] = None
    extraction:     float = 0.0
    retrieval:      float = 0.0
    recency:        float = 0.0
    final:          float = 0.0


# ── Confidence model (§6) ────────────────────────────────────────────────────

def _recency_confidence(row: KnowledgeItem) -> float:
    if row.knowledge_type in _IMMUTABLE_TYPES:
        return 1.0
    if row.knowledge_type not in _VOLATILE_TYPES:
        return 1.0
    anchor = row.valid_from or row.created_at
    if anchor is None:
        return 1.0
    age_days = max(0.0, (utcnow() - anchor).total_seconds() / 86400.0)
    return round(0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS), 4)


_EXACT_KEY_RETRIEVAL  = 1.0   # candidate matched on an exact knowledge_key
_STRUCTURED_RETRIEVAL = 0.5   # candidate gathered only by a broad field filter


def _score_candidate(cand: _Candidate, exact_key_match: bool) -> None:
    row = cand.row
    cand.extraction = float(row.confidence) if row.confidence is not None else 0.5
    # Retrieval strength: semantic similarity if we have it; a full signal only
    # for an exact-key structured hit; otherwise a moderate signal for a row that
    # merely matched a broad type/owner filter (so the confidence floor still
    # screens out weak, untargeted candidates — Plan 2 §6).
    if cand.semantic_score is not None:
        cand.retrieval = float(cand.semantic_score)
    elif exact_key_match:
        cand.retrieval = _EXACT_KEY_RETRIEVAL
    else:
        cand.retrieval = _STRUCTURED_RETRIEVAL
    cand.recency = _recency_confidence(row)
    cand.final = round(0.4 * cand.extraction + 0.4 * cand.retrieval + 0.2 * cand.recency, 4)


# ── Candidate gathering ──────────────────────────────────────────────────────

def _structured_candidates(
    db: Session, intent, user_id: int, only_active: bool
) -> list[KnowledgeItem]:
    rows: dict[int, KnowledgeItem] = {}
    types = intent.knowledge_types or [None]
    for kt in types:
        for row in crud.list_knowledge_by_filters(
            db, knowledge_type=kt, only_active=only_active, user_id=user_id, limit=50
        ):
            rows[row.id] = row
    return list(rows.values())


def _semantic_candidates(
    db: Session, intent, user_id: int, only_active: bool
) -> list[tuple[KnowledgeItem, float]]:
    query_emb = embedder.encode(intent.masked_query)
    if query_emb is None:
        return []
    out: list[tuple[KnowledgeItem, float]] = []
    types = intent.knowledge_types or [None]
    seen: set[int] = set()
    for kt in types:
        hits = search_similar_knowledge(
            db, query_emb, knowledge_type=kt, only_active=only_active,
            limit=10, min_score=_SEM_LOW, user_id=user_id,
        )
        for h in hits:
            if h["id"] in seen:
                continue
            row = crud.get_knowledge_by_id(db, h["id"])
            if row is not None:
                seen.add(h["id"])
                out.append((row, h["score"]))
    return out


# ── Ranking (§1, §6) ─────────────────────────────────────────────────────────

def _rank(cands: list[_Candidate]) -> list[_Candidate]:
    def sort_key(c: _Candidate):
        bucket = round(c.final / CONFIDENCE_EPSILON)
        precedence = SOURCE_PRECEDENCE.get(c.row.source_type or "", 0)
        anchor = c.row.valid_from or c.row.created_at or datetime.min
        # Higher bucket, then higher source precedence (tie-break), then most
        # recent, then raw final as a final discriminator.
        return (-bucket, -precedence, -anchor.timestamp() if anchor != datetime.min else 0, -c.final)
    return sorted(cands, key=sort_key)


# ── Conflict detection (§8) ──────────────────────────────────────────────────

def _detect_conflict(ranked: list[_Candidate], db: Session) -> bool:
    if len(ranked) < 2:
        return False
    top, second = ranked[0].row, ranked[1].row

    # Anomaly: two active rows somehow share a key (should be impossible given
    # the partial unique index, but surfaced rather than hidden if it occurs).
    if top.knowledge_key and top.knowledge_key == second.knowledge_key:
        logger.warning(f"Conflict: two active rows share key={top.knowledge_key}")
        return True

    # Ambiguous-band similarity between different chains -> adjudicate.
    if top.embedding and second.embedding:
        try:
            sim = _cosine_similarity(top.embedding, second.embedding)
        except Exception:
            sim = 0.0
        if _SEM_LOW <= sim < _SEM_HIGH:
            rel = _adjudicate(second, top)
            if rel == "CONFLICT":
                logger.info(f"Conflict adjudicated between id={top.id} and id={second.id}")
                return True
    return False


# ── Result construction ──────────────────────────────────────────────────────

def _value_ref(row: KnowledgeItem, is_secret_request: bool) -> str:
    """Masked text for normal facts; a vault_token reference for secrets and any
    credential_reference row — NEVER a decrypted value (Plan 2 §10)."""
    if is_secret_request or row.knowledge_type == "credential_reference":
        token = (row.extra_data or {}).get("vault_token")
        return f"vault_token:{token}" if token else "vault_token:UNAVAILABLE"
    return row.title_masked or ""


def _to_result(cand: _Candidate, is_secret_request: bool, conflict_flag: bool) -> KnowledgeResult:
    row = cand.row
    return KnowledgeResult(
        knowledge_id=row.id,
        value_ref=_value_ref(row, is_secret_request),
        knowledge_type=row.knowledge_type,
        knowledge_key=row.knowledge_key,
        version=row.version,
        confidence=cand.final,
        valid_from=str(row.valid_from) if row.valid_from else None,
        valid_to=str(row.valid_to) if row.valid_to else None,
        source_type=row.source_type,
        source_id=row.source_id,
        conflict_flag=conflict_flag,
        resolution_method=row.resolution_method,
    )


# ── Audit (§1) ───────────────────────────────────────────────────────────────

def _write_retrieval_audit(intent, results: list[KnowledgeResult], outcome: str, user_id: int) -> None:
    """One AuditEvent(event_type="RETRIEVAL") per retrieval call, via an
    independent session (same durability pattern as vault/LLM audit) so it
    survives regardless of the caller's transaction."""
    from database.connection import SessionLocal
    from database.models import AuditEvent

    session = SessionLocal()
    try:
        top_id = results[0].knowledge_id if results else None
        session.add(AuditEvent(
            event_type="RETRIEVAL",
            actor="system",
            resource_type="KnowledgeItem",
            resource_id=top_id,
            outcome=outcome,
            event_data={
                "query_type": intent.query_type,
                "is_secret_request": intent.is_secret_request,
                "classifier_status": intent.classifier_status,
                "result_count": len(results),
                "knowledge_keys": [r.knowledge_key for r in results],
                "conflict": any(r.conflict_flag for r in results),
            },
        ))
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"Retrieval audit write failed: {e}")
    finally:
        session.close()


# ── Public entry point ───────────────────────────────────────────────────────

def retrieve(
    query: str,
    db: Session,
    user_id: int = 1,
    as_of: Optional[datetime] = None,
    limit: int = 5,
) -> dict:
    """
    Full retrieval pipeline. Returns:
        {
          "status": "ok" | "insufficient_confidence",
          "intent": {...},
          "results": [KnowledgeResult-as-dict, ...],
          "conflict": bool,
        }
    Never raises for normal "no answer" cases — those return an empty result set.
    """
    intent = knowledge_intent_service.classify(query)
    only_active = as_of is None

    structured = _structured_candidates(db, intent, user_id, only_active)
    semantic = _semantic_candidates(db, intent, user_id, only_active)

    # Exact-key hits: any search term that resolves to a live knowledge_key is a
    # targeted structured match (full retrieval strength), unlike a broad filter.
    exact_key_ids: set[int] = set()
    for term in intent.search_terms:
        row = crud.get_active_knowledge_by_key(db, str(term), user_id=user_id)
        if row is not None:
            exact_key_ids.add(row.id)

    # ── Candidate merge (union + de-dup by id) ───────────────────────────────
    merged: dict[int, _Candidate] = {}
    for row in structured:
        merged[row.id] = _Candidate(row=row)
    for row, score in semantic:
        if row.id in merged:
            merged[row.id].semantic_score = score
        else:
            merged[row.id] = _Candidate(row=row, semantic_score=score)

    candidates = list(merged.values())

    # ── Version resolution ───────────────────────────────────────────────────
    # Active-only is already enforced by the queries above. For as-of queries we
    # resolved over all versions, then collapse to the version valid at `as_of`
    # per knowledge_key (Plan 2 §7).
    if as_of is not None:
        candidates = _resolve_as_of(candidates, as_of, db, user_id)

    # ── Scoring + ranking ────────────────────────────────────────────────────
    for cand in candidates:
        _score_candidate(cand, exact_key_match=cand.row.id in exact_key_ids)
    ranked = _rank(candidates)

    # ── Confidence floor (§6) ────────────────────────────────────────────────
    qualifying = [c for c in ranked if c.final >= CONFIDENCE_FLOOR]
    if not qualifying:
        _write_retrieval_audit(intent, [], "INSUFFICIENT_CONFIDENCE", user_id)
        return {
            "status": "insufficient_confidence",
            "intent": _intent_dict(intent),
            "results": [],
            "conflict": False,
        }

    # ── Conflict detection (§8) ──────────────────────────────────────────────
    conflict = _detect_conflict(qualifying, db)
    if conflict:
        # Surface BOTH conflicting candidates rather than silently picking one.
        selected = qualifying[:2]
        results = [_to_result(c, intent.is_secret_request, True) for c in selected]
    else:
        selected = qualifying[:limit]
        results = [_to_result(c, intent.is_secret_request, False) for c in selected]

    _write_retrieval_audit(intent, results, "SUCCESS", user_id)
    return {
        "status": "ok",
        "intent": _intent_dict(intent),
        "results": [asdict(r) for r in results],
        "conflict": conflict,
    }


def _resolve_as_of(
    candidates: list[_Candidate], as_of: datetime, db: Session, user_id: int
) -> list[_Candidate]:
    keys = {c.row.knowledge_key for c in candidates if c.row.knowledge_key}
    score_by_key: dict[str, Optional[float]] = {}
    for c in candidates:
        if c.row.knowledge_key:
            # keep the strongest semantic score seen for the key
            prev = score_by_key.get(c.row.knowledge_key)
            if c.semantic_score is not None and (prev is None or c.semantic_score > prev):
                score_by_key[c.row.knowledge_key] = c.semantic_score
    resolved: list[_Candidate] = []
    for key in keys:
        row = crud.get_knowledge_as_of(db, key, as_of, user_id=user_id)
        if row is not None:
            resolved.append(_Candidate(row=row, semantic_score=score_by_key.get(key)))
    return resolved


def _intent_dict(intent) -> dict:
    return {
        "query_type": intent.query_type,
        "is_secret_request": intent.is_secret_request,
        "knowledge_types": intent.knowledge_types,
        "search_terms": intent.search_terms,
        "as_of_hint": intent.as_of_hint,
        "classifier_status": intent.classifier_status,
    }
