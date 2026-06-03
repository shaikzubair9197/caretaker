from datetime import datetime, timedelta

from database.models import (
    ActiveWindow,
    Task
)


def get_last_hour_context(db):

    one_hour_ago = (
        datetime.now()
        - timedelta(hours=1)
    )

    windows = (
        db.query(ActiveWindow)
        .filter(
            ActiveWindow.started_at >= one_hour_ago
        )
        .all()
    )

    total_focus_seconds = 0

    application_usage = {}

    for window in windows:

        duration = (
            window.duration_seconds
            or 0
        )

        total_focus_seconds += duration

        application_usage[
            window.window_title
        ] = (
            application_usage.get(
                window.window_title,
                0
            )
            + duration
        )

    sorted_apps = sorted(
        application_usage.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return {
        "focus_minutes": round(
            total_focus_seconds / 60,
            2
        ),
        "applications": [
            {
                "window": app,
                "seconds": seconds
            }
            for app, seconds in sorted_apps
        ]
    }


def get_agent_context(db):

    one_hour_ago = (
        datetime.now()
        - timedelta(hours=1)
    )

    windows = (
        db.query(ActiveWindow)
        .filter(
            ActiveWindow.started_at >= one_hour_ago
        )
        .all()
    )

    pending_tasks = (
        db.query(Task)
        .filter(
            Task.status == "pending"
        )
        .all()
    )

    total_focus_seconds = 0

    application_usage = {}

    for window in windows:

        duration = (
            window.duration_seconds
            or 0
        )

        total_focus_seconds += duration

        application_usage[
            window.window_title
        ] = (
            application_usage.get(
                window.window_title,
                0
            )
            + duration
        )

    sorted_apps = sorted(
        application_usage.items(),
        key=lambda x: x[1],
        reverse=True
    )

    top_application = None

    if sorted_apps:

        top_application = sorted_apps[0][0]

    return {

        "current_time": str(
            datetime.now()
        ),

        "focus_minutes": round(
            total_focus_seconds / 60,
            2
        ),

        "pending_task_count": len(
            pending_tasks
        ),

        "pending_tasks": [
            task.description
            for task in pending_tasks
        ],

        "top_application":
            top_application,

        "applications": [
            {
                "window": app,
                "seconds": seconds
            }
            for app, seconds in sorted_apps
        ]
    }