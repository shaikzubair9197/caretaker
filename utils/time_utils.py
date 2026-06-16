from datetime import datetime, timezone


def utcnow() -> datetime:
    """
    Return the current UTC time as a timezone-naive datetime.

    All Caretaker models use TIMESTAMP WITHOUT TIME ZONE columns (SQLAlchemy
    DateTime without timezone=True).  Storing a timezone-aware object causes
    PostgreSQL to strip the tzinfo silently; storing a local-time naive object
    gives wrong values on non-UTC hosts.  Using naive UTC everywhere keeps
    values correct and avoids TypeError when comparing datetimes from different
    sources (e.g. cleanup_stale_sessions cutoff vs stored started_at).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
