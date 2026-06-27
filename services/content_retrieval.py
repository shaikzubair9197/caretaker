"""
content_retrieval — generic, staged, explainable ranking over the Content
Retrieval catalog (Document Retrieval plan — Phase 2).

Reads ONLY the served set (is_active AND index_status='INDEXED') — it never
interprets queue semantics and never knows which provider produced a row
(design rule #3, #8). Meeting Prep is the first consumer; Search / Chat / Copilot
reuse the same `RetrievalQuery` + `retrieve()` with no API change (rule #9).

Pipeline (each stage individually replaceable — rule #10):
    Candidate (adaptive pool over served)
      -> Feature extraction (semantic, keyword, entity, filename, recency, source)
      -> Normalisation (each signal min-max to 0..1)
      -> Scoring (weighted sum)
      -> Diversification (DiversificationStrategy: folder-cluster signal)
      -> Threshold + Top-N
      -> Explanation (per-result reasons + final_score_breakdown — rule #15)

Embeddings are optional: if the query or every candidate lacks an embedding the
semantic signal is dropped and the remaining weights are renormalised (rule:
embedding-optional fallback). Results are cached in-process by
(query_hash + catalog generation) for CONTENT_CACHE_TTL_SECONDS; the generation
is refreshed every CONTENT_GENERATION_REFRESH_SECONDS so the cache self-
invalidates after a catalog mutation (rule #14). One RETRIEVAL audit is written
per call via an independent session (never content/paths/PII).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import IndexedContent
from embeddings.embedder import encode
from embeddings.vector_search import _cosine_similarity
from services import content_catalog
from services.content_entities import EntityRegistry
from services.content_processor import _extract_keywords  # reuse the doc tokenizer for query/doc symmetry
from utils.config import settings
from utils.logger import get_logger

logger = get_logger("services.content_retrieval")

# Core (pre-diversification) signals + their config weights. Folder is applied
# separately in the diversification stage so it stays swappable.
_CORE_SIGNALS = ("semantic", "keyword", "entity", "filename", "recency", "source")

# "Match" signals are direct query↔document evidence; "context" signals
# (recency/source/folder) are tie-breakers/boosters. They differ only in the
# min-max degenerate case (no spread across the pool): a uniformly-present match
# signal is fully present (1.0) so a lone/uniform genuine match still clears the
# score threshold, whereas a uniform context signal has no discriminating power
# and contributes nothing (0.0) — so non-matching documents are not surfaced.
_MATCH_SIGNALS = {"semantic", "keyword", "entity", "filename"}


def _core_weights() -> dict[str, float]:
    return {
        "semantic": settings.CONTENT_W_SEMANTIC,
        "keyword": settings.CONTENT_W_KEYWORD,
        "entity": settings.CONTENT_W_ENTITY,
        "filename": settings.CONTENT_W_FILENAME,
        "recency": settings.CONTENT_W_RECENCY,
        "source": settings.CONTENT_W_SOURCE,
    }


# ── Generic query (future consumers need no API change — rule #9) ─────────────
@dataclass
class RetrievalQuery:
    text: str = ""
    entities: list[dict] = field(default_factory=list)        # [{"type","value"}]
    keywords: list[str] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    date: Optional[datetime] = None
    preferred_sources: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    top_n: Optional[int] = None

    def is_empty(self) -> bool:
        return not (self.text.strip() or self.keywords or self.entities)

    def cache_key(self) -> str:
        payload = {
            "text": self.text,
            "keywords": sorted(self.keywords),
            "entities": sorted((e.get("type", ""), e.get("value", "")) for e in self.entities),
            "participants": sorted(p.lower() for p in self.participants),
            "preferred_sources": sorted(s.upper() for s in self.preferred_sources),
            "filters": self.filters,
            "top_n": self.top_n,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Common meeting/filler words that are noise for relevance — they rarely identify
# the RIGHT document. Dropped from the query side only (document keywords are
# untouched). Corpus-frequent terms are additionally handled by IDF at query time.
_GENERIC_QUERY_TERMS = {
    "using", "use", "used", "via", "etc", "todo", "misc", "draft", "copy",
    "new", "old", "final", "note", "notes", "doc", "docs", "file", "files",
    "test", "testing", "phase", "stuff", "thing", "things", "task", "tasks",
    "agenda", "discussion", "demo", "intro", "overview", "general",
}


def build_meeting_query(
    title: str = "",
    agenda: str = "",
    attendees: Optional[list[str]] = None,
    date: Optional[datetime] = None,
    top_n: Optional[int] = None,
) -> RetrievalQuery:
    """Build a RetrievalQuery from meeting-prep fields. Keywords/entities are
    extracted with the SAME deterministic functions used at index time so query
    and document signals are directly comparable. Generic filler words are dropped
    from the query so they cannot create spurious matches."""
    text = " ".join(t for t in (title, agenda) if t).strip()
    keywords = [k for k in (_extract_keywords(text, "") if text else []) if k not in _GENERIC_QUERY_TERMS]
    entities = [e.as_dict() for e in EntityRegistry.extract_all(text)] if text else []
    participants = [a.strip() for a in (attendees or []) if a and a.strip()]
    return RetrievalQuery(
        text=text,
        keywords=keywords,
        entities=entities,
        participants=participants,
        date=date,
        top_n=top_n,
    )


# ── Internal candidate ────────────────────────────────────────────────────────
@dataclass
class _Candidate:
    row: IndexedContent
    raw: dict[str, float] = field(default_factory=dict)
    norm: dict[str, float] = field(default_factory=dict)
    contributions: dict[str, float] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)   # human reason values
    score: float = 0.0
    has_match: bool = False   # True only on a real lexical or strong-semantic match


# ── Fuzzy / distinctiveness helpers (typo-tolerant, generic-term aware) ───────
def _fuzzy_hit(term: str, vocab: set[str]) -> bool:
    """True if `term` matches any token in `vocab` exactly, or fuzzily for
    reasonably-long tokens (catches typos like appolo↔apollo)."""
    if term in vocab:
        return True
    if len(term) < 5:
        return False  # too short to fuzzy-match safely
    ratio = settings.CONTENT_FUZZY_RATIO
    for token in vocab:
        if len(token) >= 5 and difflib.SequenceMatcher(None, term, token).ratio() >= ratio:
            return True
    return False


# Below this pool size there are too few documents to judge a term's
# corpus-frequency, so every term is treated as distinctive (weight 1.0).
_MIN_DOCS_FOR_IDF = 6


def _compute_idf(q_keywords: set[str], doc_kw_sets: list[set[str]], n: int) -> dict[str, float]:
    """Per-query-term distinctiveness in 0..1. With a large-enough corpus, terms
    appearing (fuzzily) in more than GENERIC_DF_RATIO of documents are generic →
    weight 0; rare terms → ~1. With a tiny corpus, every term stays distinctive."""
    if n < _MIN_DOCS_FOR_IDF:
        return {term: 1.0 for term in q_keywords}
    idf: dict[str, float] = {}
    denom = math.log(n + 1) or 1.0
    for term in q_keywords:
        df = sum(1 for dkw in doc_kw_sets if _fuzzy_hit(term, dkw))
        if df / n > settings.CONTENT_GENERIC_DF_RATIO:
            idf[term] = 0.0
        else:
            idf[term] = math.log((n + 1) / (df + 1)) / denom
    return idf


# ── Diversification (abstraction — rule #12) ──────────────────────────────────
class DiversificationStrategy(ABC):
    @abstractmethod
    def apply(self, candidates: list[_Candidate]) -> None:
        """Mutate each candidate's score/contributions with a diversity signal."""


class FolderClusterDiversifier(DiversificationStrategy):
    """Boost a document by FOLDER_BOOST × (number of candidates in its folder):
    the folder with the most relevant hits is likely the meeting's primary
    project area. Normalised across the pool and weighted by CONTENT_W_FOLDER."""

    def __init__(self, folder_boost: float, folder_weight: float) -> None:
        self._boost = folder_boost
        self._weight = folder_weight

    def apply(self, candidates: list[_Candidate]) -> None:
        sizes = Counter(c.row.folder or "" for c in candidates)
        raw = {id(c): self._boost * sizes[c.row.folder or ""] for c in candidates}
        # Folder is a context signal: only boost when there is actual clustering
        # spread, never when every candidate sits in an equally-sized cluster.
        norm = _normalize(raw, presence=False)
        for c in candidates:
            n = norm[id(c)]
            contribution = self._weight * n
            c.norm["folder"] = n
            c.contributions["folder"] = contribution
            c.score += contribution
            c.detail["folder"] = {"folder": c.row.folder, "cluster_size": sizes[c.row.folder or ""]}


def _make_diversifier() -> DiversificationStrategy:
    # Single registered strategy today; Source/Project/Repository variants plug in
    # here later with no pipeline change.
    return FolderClusterDiversifier(settings.CONTENT_FOLDER_BOOST, settings.CONTENT_W_FOLDER)


# ── Normalisation helper (min-max to 0..1) ────────────────────────────────────
def _normalize(values: dict, presence: bool) -> dict:
    """Min-max normalise a signal across the candidate pool. Degenerate case (no
    spread): a match signal that is uniformly present maps to 1.0; everything
    else maps to 0.0. See _MATCH_SIGNALS."""
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi <= lo:
        return {k: (1.0 if (presence and v > 0) else 0.0) for k, v in values.items()}
    span = hi - lo
    return {k: (v - lo) / span for k, v in values.items()}


# ── In-process caches (rule #14) ──────────────────────────────────────────────
_result_cache: dict[str, tuple[float, list[dict]]] = {}
_gen_cache: dict[str, Any] = {"value": None, "fetched_at": 0.0}


def clear_cache() -> None:
    _result_cache.clear()
    _gen_cache["value"] = None
    _gen_cache["fetched_at"] = 0.0


def _current_generation(db: Session) -> int:
    now = time.monotonic()
    if _gen_cache["value"] is None or now - _gen_cache["fetched_at"] > settings.CONTENT_GENERATION_REFRESH_SECONDS:
        try:
            _gen_cache["value"] = content_catalog.read_generation(db)
        except Exception as e:  # noqa: BLE001 - generation is best-effort cache keying
            logger.warning(f"Generation read failed, using 0: {e}")
            _gen_cache["value"] = 0
        _gen_cache["fetched_at"] = now
    return int(_gen_cache["value"])


# ── Public entry point ────────────────────────────────────────────────────────
def retrieve(db: Session, query: RetrievalQuery) -> list[dict]:
    """Rank served content against `query`. Returns typed result dicts
    (rule #8): [{type:"document", content_id, filename, folder, extension,
    size_bytes, modified_at, score, reasons, final_score_breakdown}]. Never raises
    for normal "no match" — returns []. No filesystem path is ever included."""
    if query.is_empty():
        _write_retrieval_audit(query, [], "EMPTY")
        return []

    gen = _current_generation(db)
    cache_key = f"{query.cache_key()}::{gen}"
    cached = _result_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] <= settings.CONTENT_CACHE_TTL_SECONDS:
        results = cached[1]
        _write_retrieval_audit(query, results, "OK_CACHED")
        return results

    try:
        results = _run_pipeline(db, query)
    except Exception as e:  # noqa: BLE001 - retrieval must never break a caller (fail-closed)
        logger.warning(f"Retrieval pipeline failed: {e}")
        _write_retrieval_audit(query, [], "ERROR")
        return []

    _result_cache[cache_key] = (time.monotonic(), results)
    _write_retrieval_audit(query, results, "OK" if results else "EMPTY")
    return results


# ── Pipeline stages ───────────────────────────────────────────────────────────
def _run_pipeline(db: Session, query: RetrievalQuery) -> list[dict]:
    top_n = query.top_n or settings.CONTENT_TOP_N

    # 1) Candidate pool (adaptive — rule #11).
    pool_size = max(top_n * settings.CONTENT_CANDIDATE_MULTIPLIER, settings.CONTENT_CANDIDATE_MIN)
    rows = content_catalog.list_served(db, limit=pool_size)
    if not rows:
        return []
    candidates = [_Candidate(row=r) for r in rows]

    # 2) Feature extraction.
    q_keywords = {k.casefold() for k in query.keywords}
    q_entities = {(e.get("type", ""), str(e.get("value", "")).casefold()) for e in query.entities}
    q_embedding = encode(query.text) if query.text.strip() else None
    semantic_active = q_embedding is not None and any(c.row.embedding for c in candidates)

    # Distinctiveness of each query keyword (down-weight generic terms like "using").
    doc_kw_sets = [{str(k).casefold() for k in (c.row.keywords or [])} for c in candidates]
    idf = _compute_idf(q_keywords, doc_kw_sets, len(candidates))
    # Distinctive terms drive lexical matching: non-generic keywords + every entity value.
    distinctive = {t for t in q_keywords if idf.get(t, 0.0) > 0.0} | {v for _, v in q_entities}

    for c, dkw in zip(candidates, doc_kw_sets):
        _extract_features(c, query, q_keywords, q_entities, distinctive, idf, dkw,
                          q_embedding, semantic_active)

    # 2b) STRICT GATE: keep only documents with a real match (lexical, or strong
    #     semantic). Folder/recency/source are tie-breakers — never a reason to
    #     surface a document. No genuine match → return nothing (no filler).
    matched = [c for c in candidates if c.has_match]
    if not matched:
        return []
    candidates = matched

    # 3) Normalise each signal across the matched set.
    active_signals = [s for s in _CORE_SIGNALS if s != "semantic" or semantic_active]
    for signal in active_signals:
        norm = _normalize(
            {id(c): c.raw.get(signal, 0.0) for c in candidates},
            presence=signal in _MATCH_SIGNALS,
        )
        for c in candidates:
            c.norm[signal] = norm[id(c)]

    # 4) Score (weighted sum of core signals, weights renormalised if semantic dropped).
    weights = _resolve_weights(semantic_active)
    for c in candidates:
        for signal in active_signals:
            contribution = weights[signal] * c.norm.get(signal, 0.0)
            c.contributions[signal] = contribution
        c.score = sum(c.contributions.values())

    # 5) Diversify (adds the folder-cluster signal).
    _make_diversifier().apply(candidates)

    # 6) Threshold + Top-N. Two gates keep the list tight: an absolute floor
    #    (MIN_SCORE) drops near-zero matches, and a relative floor (a fraction of
    #    the top score) drops the weak long tail so only genuinely-comparable
    #    matches are surfaced rather than always returning top_n filler.
    candidates.sort(key=lambda c: c.score, reverse=True)
    top_score = candidates[0].score if candidates else 0.0
    floor = max(settings.CONTENT_MIN_SCORE, top_score * settings.CONTENT_REL_SCORE_RATIO)
    kept = [c for c in candidates if c.score >= floor][:top_n]

    # 7) Explain.
    return [_explain(c) for c in kept]


def _extract_features(
    c: _Candidate,
    query: RetrievalQuery,
    q_keywords: set,
    q_entities: set,
    distinctive: set,
    idf: dict[str, float],
    doc_keywords: set,
    q_embedding: Optional[list],
    semantic_active: bool,
) -> None:
    row = c.row
    has_lexical = False

    # semantic — cosine, with an ABSOLUTE floor (below it gives no credit at all,
    # so weak "everything is vaguely similar" matches contribute nothing).
    cosine = 0.0
    if semantic_active and q_embedding is not None and row.embedding:
        try:
            cosine = max(0.0, _cosine_similarity(q_embedding, row.embedding))
        except Exception:  # noqa: BLE001
            cosine = 0.0
    floor = settings.CONTENT_SEMANTIC_FLOOR
    c.raw["semantic"] = (cosine - floor) / (1.0 - floor) if cosine >= floor and floor < 1.0 else 0.0
    # Semantic alone justifies showing a doc only when it is STRONG.
    has_strong_semantic = cosine >= settings.CONTENT_SEMANTIC_MATCH_FLOOR

    # keyword — fuzzy, IDF-weighted (generic terms contribute ~0 and don't qualify).
    kw_matched: list[str] = []
    kw_score = 0.0
    for term in q_keywords:
        weight = idf.get(term, 0.0)
        if weight <= 0.0:
            continue
        if _fuzzy_hit(term, doc_keywords):
            kw_score += weight
            kw_matched.append(_closest(term, doc_keywords))
    c.raw["keyword"] = kw_score
    c.detail["keyword"] = sorted(set(kw_matched))
    has_lexical = has_lexical or kw_score > 0.0

    # entity overlap (structured → exact; inherently distinctive).
    doc_entities = {(e.get("type", ""), str(e.get("value", "")).casefold()) for e in (row.entities or [])}
    ent_overlap = q_entities & doc_entities
    c.raw["entity"] = float(len(ent_overlap))
    c.detail["entity"] = sorted(v for _, v in ent_overlap)
    has_lexical = has_lexical or bool(ent_overlap)

    # filename — distinctive query terms appearing (exact or fuzzy) in the name.
    fname = (row.filename or "").casefold()
    fname_tokens = set(re.findall(r"[a-z0-9]{3,}", fname))
    fname_hits = {t for t in distinctive if t and (t in fname or _fuzzy_hit(t, fname_tokens))}
    c.raw["filename"] = float(len(fname_hits))
    c.detail["filename"] = sorted(fname_hits)
    has_lexical = has_lexical or bool(fname_hits)

    # recency — newer is higher (epoch seconds; normalised later). Tie-breaker only.
    when = row.modified_at or row.created_at
    c.raw["recency"] = when.timestamp() if isinstance(when, datetime) else 0.0

    # source priority + preferred-source bonus (rule #13). Tie-breaker only.
    priority = settings.CONTENT_SOURCE_PRIORITY.get((row.source_type or "").upper(), 0)
    if row.source_type and row.source_type.upper() in {s.upper() for s in query.preferred_sources}:
        priority += 1
    c.raw["source"] = float(priority)

    c.has_match = has_lexical or has_strong_semantic


def _closest(term: str, vocab: set[str]) -> str:
    """The vocab token that best matches `term` (for display)."""
    if term in vocab:
        return term
    best, best_ratio = term, 0.0
    for token in vocab:
        r = difflib.SequenceMatcher(None, term, token).ratio()
        if r > best_ratio:
            best, best_ratio = token, r
    return best


def _resolve_weights(semantic_active: bool) -> dict[str, float]:
    weights = _core_weights()
    if semantic_active:
        return weights
    # Drop semantic and redistribute its weight across the remaining core signals,
    # preserving the total core share so folder's slice stays intact.
    total = sum(weights.values())
    semantic_w = weights.pop("semantic")
    remaining = total - semantic_w
    if remaining > 0:
        scale = total / remaining
        weights = {k: v * scale for k, v in weights.items()}
    weights["semantic"] = 0.0
    return weights


_REASON_LABELS = {
    "semantic": "content similarity",
    "keyword": "keyword match",
    "entity": "shared entity",
    "filename": "filename match",
    "recency": "recently modified",
    "source": "source priority",
    "folder": "related folder",
}


def _reason_value(signal: str, c: _Candidate) -> str:
    if signal in ("keyword", "entity", "filename"):
        items = c.detail.get(signal) or []
        return ", ".join(items[:5]) if items else _REASON_LABELS[signal]
    if signal == "folder":
        info = c.detail.get("folder") or {}
        folder = info.get("folder") or "(root)"
        return f"{folder} ({info.get('cluster_size', 1)} related)"
    if signal == "recency":
        when = c.row.modified_at or c.row.created_at
        return when.isoformat() if isinstance(when, datetime) else _REASON_LABELS[signal]
    if signal == "source":
        return c.row.root_label or c.row.source_type or _REASON_LABELS[signal]
    return _REASON_LABELS[signal]


def _explain(c: _Candidate) -> dict:
    weights = _core_weights()
    weights["folder"] = settings.CONTENT_W_FOLDER
    breakdown = {sig: round(c.contributions.get(sig, 0.0), 6) for sig in list(_CORE_SIGNALS) + ["folder"]}
    reasons = [
        {
            "reason_type": sig,
            "reason_value": _reason_value(sig, c),
            "score": round(c.norm.get(sig, 0.0), 4),
            "weight": round(weights.get(sig, 0.0), 4),
        }
        for sig in sorted(breakdown, key=lambda s: breakdown[s], reverse=True)
        if breakdown[sig] > 0
    ]
    return {
        "type": "document",
        "content_id": c.row.id,
        "filename": c.row.filename,
        "folder": c.row.folder,
        "extension": c.row.extension,
        "size_bytes": c.row.size_bytes,
        "modified_at": c.row.modified_at.isoformat() if isinstance(c.row.modified_at, datetime) else None,
        "score": round(c.score, 6),
        "reasons": reasons,
        "final_score_breakdown": breakdown,
    }


# ── Audit (independent session — durable regardless of caller; never PII) ─────
def _write_retrieval_audit(query: RetrievalQuery, results: list[dict], outcome: str) -> None:
    """One AuditEvent(event_type="RETRIEVAL") per retrieve() call. Records only
    counts + returned content ids/scores — never query text, keywords, entities,
    file content or paths (mirrors knowledge_retrieval_service._write_retrieval_audit)."""
    from database.connection import SessionLocal
    from database.models import AuditEvent

    session = SessionLocal()
    try:
        top_id = results[0]["content_id"] if results else None
        session.add(AuditEvent(
            event_type="RETRIEVAL",
            actor="system",
            resource_type="IndexedContent",
            resource_id=top_id,
            outcome=outcome[:20],
            event_data={
                "result_count": len(results),
                "content_ids": [r["content_id"] for r in results],
                "scores": [r["score"] for r in results],
                "query_keyword_count": len(query.keywords),
                "query_entity_count": len(query.entities),
            },
        ))
        session.commit()
    except Exception as e:  # noqa: BLE001
        session.rollback()
        logger.warning(f"Retrieval audit write failed: {e}")
    finally:
        session.close()
