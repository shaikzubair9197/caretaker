from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from utils.time_utils import utcnow

from database.models import (
    Task,
    ActiveWindow,
    Memory,
    KnowledgeItem,
    AuditEvent,
)


# --------------------------------------------------
# TASKS
# --------------------------------------------------

def create_task(
    db: Session,
    description: str,
    priority: str,
    user_id: int = 1,
) -> Task:

    task = Task(
        description=description,
        priority=priority,
        status="pending",
        user_id=user_id,
    )

    db.add(task)

    try:
        db.commit()
    except IntegrityError:
        # A concurrent insert won the race on uq_task_description
        # (user_id, description). Return the existing row instead of 500ing.
        db.rollback()
        existing = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.description == description,
            )
            .first()
        )
        if existing is not None:
            return existing
        raise

    db.refresh(task)

    return task


def get_all_tasks(
    db: Session
) -> list[Task]:

    return (
        db.query(Task)
        .order_by(Task.id.desc())
        .all()
    )


# --------------------------------------------------
# ACTIVE WINDOW SESSIONS
# --------------------------------------------------

def start_window_session(
    db: Session,
    window_title: str
) -> ActiveWindow:

    session = ActiveWindow(
        window_title=window_title,
        started_at=utcnow(),
        created_at=utcnow(),
    )

    db.add(session)

    db.commit()

    db.refresh(session)

    return session


def get_active_session(
    db: Session
):

    return (
        db.query(ActiveWindow)
        .filter(
            ActiveWindow.ended_at.is_(None)
        )
        .order_by(
            ActiveWindow.id.desc()
        )
        .first()
    )


def end_window_session(
    db: Session,
    session_id: int
):

    session = (
        db.query(ActiveWindow)
        .filter(
            ActiveWindow.id == session_id
        )
        .first()
    )

    if session is None:
        return None

    # Defensive protection for old/bad rows
    if session.started_at is None:
        session.started_at = utcnow()

    session.ended_at = utcnow()

    session.duration_seconds = int(
        (
            session.ended_at -
            session.started_at
        ).total_seconds()
    )

    db.commit()

    db.refresh(session)

    return session


def cleanup_stale_sessions(
    db: Session,
    stale_after_minutes: int = 30,
) -> int:
    """
    Close ActiveWindow rows that are still open (ended_at IS NULL) yet started
    more than `stale_after_minutes` ago — sessions the daemon never ended because
    it stopped (sleep/crash/quit) while that window was focused.

    The daemon only posts on window switch, so there is no activity signal after
    started_at. We therefore CAP the attributed focus at the stale window rather
    than counting the entire wall-clock gap (which could be hours/days of sleep)
    as focus time.

    Returns the number of rows closed.
    """
    cutoff = utcnow() - timedelta(minutes=stale_after_minutes)
    stale = (
        db.query(ActiveWindow)
        .filter(
            ActiveWindow.ended_at.is_(None),
            ActiveWindow.started_at < cutoff,
        )
        .all()
    )
    for row in stale:
        # No evidence of activity past started_at → cap, don't count the gap.
        capped_end = row.started_at + timedelta(minutes=stale_after_minutes)
        row.ended_at = capped_end
        row.duration_seconds = int(
            (capped_end - row.started_at).total_seconds()
        )
    if stale:
        db.commit()
    return len(stale)


def get_all_window_events(
    db: Session
):

    return (
        db.query(ActiveWindow)
        .order_by(
            ActiveWindow.id.desc()
        )
        .all()
    )


def get_window_by_id(
    db: Session,
    session_id: int
):

    return (
        db.query(ActiveWindow)
        .filter(
            ActiveWindow.id == session_id
        )
        .first()
    )
def create_memory(db: Session, text: str, memory_type: str = "general"):
    memory = Memory(
        text=text,
        type=memory_type
    )

    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def get_memories(db: Session):
    return db.query(Memory).order_by(Memory.id.desc()).all()


# --------------------------------------------------
# KNOWLEDGE ITEMS  (Plan 2 — retrieval & versioning)
# --------------------------------------------------
# These are the first read/update functions KnowledgeItem has ever had.
# Plan 1 only ever INSERTed (knowledge_persistence_service.persist). All reads
# default to is_active=True so callers see only the current version of each
# fact unless they explicitly ask for history / an as-of snapshot.

def get_knowledge_by_id(db: Session, knowledge_id: int) -> Optional[KnowledgeItem]:
    return db.query(KnowledgeItem).filter(KnowledgeItem.id == knowledge_id).first()


def get_active_knowledge_by_key(
    db: Session,
    knowledge_key: str,
    user_id: int = 1,
    for_update: bool = False,
) -> Optional[KnowledgeItem]:
    """
    The single active version for a knowledge_key, or None.

    for_update=True takes a row lock (SELECT ... FOR UPDATE) so concurrent
    ingestions of the same fact cannot both deactivate/activate — used by the
    evolution service's transactional supersession. On SQLite (tests) SQLAlchemy
    omits FOR UPDATE, which is harmless there.
    """
    query = (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.knowledge_key == knowledge_key,
            KnowledgeItem.is_active.is_(True),
            KnowledgeItem.user_id == user_id,
        )
        .order_by(KnowledgeItem.version.desc())
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def list_active_knowledge_by_key(
    db: Session,
    knowledge_key: str,
    user_id: int = 1,
) -> list[KnowledgeItem]:
    """
    Every active row for a key. Normally returns 0 or 1 (the partial unique
    index guarantees at most one), but conflict detection asks for the list so
    it can surface a genuine >1 anomaly rather than silently picking one.
    """
    return (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.knowledge_key == knowledge_key,
            KnowledgeItem.is_active.is_(True),
            KnowledgeItem.user_id == user_id,
        )
        .order_by(KnowledgeItem.version.desc())
        .all()
    )


def list_all_versions_by_key(
    db: Session,
    knowledge_key: str,
    user_id: int = 1,
) -> list[KnowledgeItem]:
    """
    Every version — active AND superseded — for a knowledge_key, newest first.
    Mirrors list_active_knowledge_by_key but WITHOUT the is_active filter; used by
    the Follow-up Center's version-history drawer (Plan 3 R8, read-only/masked).
    """
    return (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.knowledge_key == knowledge_key,
            KnowledgeItem.user_id == user_id,
        )
        .order_by(KnowledgeItem.version.desc())
        .all()
    )


def list_audit_for_action(db: Session, action_id: int, limit: int = 200) -> list:
    """
    The audit timeline for one AgentAction draft (Plan 3 R7/R8): events recorded
    directly against the action (ACTION_APPROVED/EXECUTED/FAILED/DISMISSED,
    DRAFT_EDITED) PLUS generation events recorded against the originating
    KnowledgeItem that carry this action_id in event_data (DRAFT_GENERATED /
    DRAFT_GATE_DECISION). Oldest-first. Returns masked event_data only — never
    decrypts.
    """
    return (
        db.query(AuditEvent)
        .filter(
            or_(
                and_(AuditEvent.resource_type == "AgentAction", AuditEvent.resource_id == action_id),
                AuditEvent.event_data["action_id"].astext == str(action_id),
            )
        )
        .order_by(AuditEvent.id.asc())
        .limit(limit)
        .all()
    )


def list_knowledge_by_filters(
    db: Session,
    knowledge_type: Optional[str] = None,
    owner_token: Optional[str] = None,
    status: Optional[str] = None,
    only_active: bool = True,
    user_id: int = 1,
    limit: int = 50,
) -> list[KnowledgeItem]:
    """Structured retrieval: filter the current knowledge set by exact fields."""
    query = db.query(KnowledgeItem).filter(KnowledgeItem.user_id == user_id)
    if only_active:
        query = query.filter(KnowledgeItem.is_active.is_(True))
    if knowledge_type is not None:
        query = query.filter(KnowledgeItem.knowledge_type == knowledge_type)
    if owner_token is not None:
        query = query.filter(KnowledgeItem.owner_token == owner_token)
    if status is not None:
        query = query.filter(KnowledgeItem.status == status)
    return query.order_by(KnowledgeItem.id.desc()).limit(limit).all()


def get_knowledge_as_of(
    db: Session,
    knowledge_key: str,
    as_of: datetime,
    user_id: int = 1,
) -> Optional[KnowledgeItem]:
    """
    The version of a fact that was valid at `as_of`:
        valid_from <= as_of < (valid_to or +inf)
    Powers "what was the API key as of <date>" historical queries. Rows are
    never deleted, so history stays queryable indefinitely.
    """
    candidates = (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.knowledge_key == knowledge_key,
            KnowledgeItem.user_id == user_id,
            KnowledgeItem.valid_from.isnot(None),
            KnowledgeItem.valid_from <= as_of,
        )
        .order_by(KnowledgeItem.version.desc())
        .all()
    )
    for row in candidates:
        if row.valid_to is None or as_of < row.valid_to:
            return row
    return None


def list_knowledge_with_embeddings(
    db: Session,
    only_active: bool = True,
    knowledge_type: Optional[str] = None,
    user_id: int = 1,
) -> list[KnowledgeItem]:
    """Rows that have an embedding stored — the candidate set for semantic search."""
    query = (
        db.query(KnowledgeItem)
        .filter(
            KnowledgeItem.embedding.isnot(None),
            KnowledgeItem.user_id == user_id,
        )
    )
    if only_active:
        query = query.filter(KnowledgeItem.is_active.is_(True))
    if knowledge_type is not None:
        query = query.filter(KnowledgeItem.knowledge_type == knowledge_type)
    return query.all()
