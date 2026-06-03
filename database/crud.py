from datetime import datetime

from sqlalchemy.orm import Session

from database.models import (
    Task,
    ActiveWindow,
    Memory
)


# --------------------------------------------------
# TASKS
# --------------------------------------------------

def create_task(
    db: Session,
    description: str,
    priority: str
) -> Task:

    task = Task(
        description=description,
        priority=priority,
        status="pending"
    )

    db.add(task)

    db.commit()

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
        started_at=datetime.now(),
        created_at=datetime.now()
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
        session.started_at = datetime.now()

    session.ended_at = datetime.now()

    session.duration_seconds = int(
        (
            session.ended_at -
            session.started_at
        ).total_seconds()
    )

    db.commit()

    db.refresh(session)

    return session


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
        memory_text=text,
        memory_type=memory_type
    )

    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def get_memories(db: Session):
    return db.query(Memory).order_by(Memory.id.desc()).all()
