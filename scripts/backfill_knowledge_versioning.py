"""
Backfill knowledge_items for Plan 2 (retrieval & versioning).

Idempotent enrichment pass to run AFTER migrations/005_knowledge_versioning.sql
(or after Base.metadata.create_all adds the new columns). It:

  1. Assigns versioning defaults (version / is_active / valid_from / knowledge_key)
     to any row still missing them — mirrors the SQL backfill so installs that
     rely on create_all rather than the SQL migration are covered too.
  2. Best-effort assigns deterministic structured keys (github_pr, jira_ticket,
     credential_reference, ...) where the extra_data supports it AND no other
     active row already holds that key — never violating the partial unique
     index, never silently merging history.
  3. Generates embeddings for active rows that don't have one yet, so semantic
     retrieval works over pre-Plan-2 data.

Safe to run repeatedly. Never deletes rows. Never decrypts secrets — for
credential_reference it embeds the same metadata-only template the live pipeline
uses (Plan 2 §2).

Usage:
    cd caretaker
    python scripts/backfill_knowledge_versioning.py            # apply
    python scripts/backfill_knowledge_versioning.py --dry-run  # report only
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import SessionLocal                       # noqa: E402
from database.models import KnowledgeItem                          # noqa: E402
from services import knowledge_evolution_service as evo            # noqa: E402
from utils.logger import get_logger                                # noqa: E402
from utils.time_utils import utcnow                                # noqa: E402

logger = get_logger("scripts.backfill_knowledge_versioning")


def _active_key_taken(db, key: str, exclude_id: int) -> bool:
    return (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.knowledge_key == key,
            KnowledgeItem.is_active.is_(True),
            KnowledgeItem.id != exclude_id,
        )
        .first()
        is not None
    )


def backfill(dry_run: bool = False) -> dict:
    db = SessionLocal()
    stats = {"versioning_defaults": 0, "structured_keys": 0, "embeddings": 0, "scanned": 0}
    try:
        rows = db.query(KnowledgeItem).order_by(KnowledgeItem.id.asc()).all()
        for row in rows:
            stats["scanned"] += 1

            # 1. Versioning defaults
            if row.knowledge_key is None or row.version is None or row.is_active is None:
                row.version = row.version or 1
                row.is_active = True if row.is_active is None else row.is_active
                row.valid_from = row.valid_from or row.created_at or utcnow()
                if row.knowledge_key is None:
                    row.knowledge_key = f"legacy:{row.id}"
                    row.resolution_method = row.resolution_method or "backfill"
                stats["versioning_defaults"] += 1

            # 2. Best-effort deterministic structured key
            if (
                row.is_active
                and row.knowledge_type in evo._STRUCTURED_KEY_TYPES
                and (row.knowledge_key or "").startswith("legacy:")
            ):
                derived = evo.derive_knowledge_key(row)
                if derived and not _active_key_taken(db, derived, row.id):
                    row.knowledge_key = derived
                    row.resolution_method = "backfill_structured"
                    stats["structured_keys"] += 1

            # 3. Embedding for active rows that lack one
            if row.is_active and row.embedding is None:
                emb = evo._compute_embedding(row)
                if emb is not None:
                    row.embedding = emb
                    stats["embeddings"] += 1

        if dry_run:
            db.rollback()
            logger.info(f"[dry-run] backfill would apply: {stats}")
        else:
            db.commit()
            logger.info(f"backfill applied: {stats}")
        return stats
    except Exception:
        db.rollback()
        logger.exception("backfill failed — rolled back")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill knowledge_items for Plan 2.")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args()
    result = backfill(dry_run=args.dry_run)
    print(result)
