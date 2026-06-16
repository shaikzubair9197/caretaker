import math
from typing import Optional

from sqlalchemy.orm import Session

from database.models import Memory
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
