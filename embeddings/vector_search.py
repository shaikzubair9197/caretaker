import math
from typing import Optional

from sqlalchemy.orm import Session

from database.models import KnowledgeItem, Memory
from utils.logger import get_logger

logger = get_logger("embeddings.vector_search")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def search_similar(
    db: Session,
    query_embedding: list[float],
    limit: int = 5,
    min_score: float = 0.3,
) -> list[dict]:
    """
    Return memories ordered by cosine similarity to query_embedding.
    Falls back to empty list if no memories have embeddings stored.
    """
    memories = (
        db.query(Memory)
        .filter(Memory.embedding.isnot(None))
        .all()
    )

    if not memories:
        return []

    scored = []
    for m in memories:
        try:
            score = _cosine_similarity(query_embedding, m.embedding)
            if score >= min_score:
                scored.append((score, m))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        {
            "id": m.id,
            "text": m.text,
            "type": m.type,
            "importance": m.importance,
            "score": round(score, 4),
        }
        for score, m in scored[:limit]
    ]


def search_similar_knowledge(
    db: Session,
    query_embedding: list[float],
    knowledge_type: Optional[str] = None,
    only_active: bool = True,
    limit: int = 5,
    min_score: float = 0.3,
    user_id: int = 1,
) -> list[dict]:
    """
    Semantic retrieval over KnowledgeItem rows — same in-memory cosine pattern
    as search_similar() (no pgvector; scaling risk noted in Plan 2 §9).

    Returns the matched KnowledgeItem ids with their similarity scores. Callers
    re-load full rows so this stays a lightweight scoring pass. Never returns
    decrypted values — only references the rows by id (Plan 2 §10).
    """
    items = (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.embedding.isnot(None),
            KnowledgeItem.user_id == user_id,
        )
    )
    if only_active:
        items = items.filter(KnowledgeItem.is_active.is_(True))
    if knowledge_type is not None:
        items = items.filter(KnowledgeItem.knowledge_type == knowledge_type)
    items = items.all()

    if not items:
        return []

    scored = []
    for k in items:
        try:
            score = _cosine_similarity(query_embedding, k.embedding)
            if score >= min_score:
                scored.append((score, k))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        {
            "id": k.id,
            "knowledge_key": k.knowledge_key,
            "knowledge_type": k.knowledge_type,
            "score": round(score, 4),
        }
        for score, k in scored[:limit]
    ]
