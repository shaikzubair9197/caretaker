from fastapi import APIRouter

from database.connection import SessionLocal

from schemas.window_schema import ActiveWindowRequest

from database.crud import (
    start_window_session,
    end_window_session,
    get_active_session,
    get_all_window_events,
    cleanup_stale_sessions,
)

from services.context_service import (
    get_last_hour_context
)

router = APIRouter(
    prefix="/telemetry",
    tags=["Telemetry"]
)


@router.post("/")
def save_window(request: ActiveWindowRequest):

    db = SessionLocal()

    try:

        cleanup_stale_sessions(db)

        active_session = get_active_session(db)

        if active_session:

            if (
                active_session.window_title
                != request.window_title
            ):

                end_window_session(
                    db,
                    active_session.id
                )

                start_window_session(
                    db,
                    request.window_title
                )

        else:

            start_window_session(
                db,
                request.window_title
            )

        return {
            "status": "saved"
        }

    finally:

        db.close()


@router.get("/")
def get_windows():

    db = SessionLocal()

    try:

        return get_all_window_events(db)

    finally:

        db.close()


@router.get("/summary")
def get_context_summary():

    db = SessionLocal()

    try:

        return get_last_hour_context(db)

    finally:

        db.close()