from sqlalchemy.orm import Session

from database.crud import (
    create_task,
    get_all_tasks
)

from database.models import Task


class TaskService:

    @staticmethod
    def create(
        db: Session,
        description: str,
        priority: str,
        user_id: int = 1,
    ):
        """
        Prevent duplicate tasks before creating new one.

        Dedup is scoped to user_id so it matches the (user_id, description)
        unique constraint — a description-only match would return another
        user's task.
        """

        existing_task = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.description == description,
            )
            .first()
        )

        if existing_task:
            return existing_task

        return create_task(
            db,
            description,
            priority,
            user_id=user_id,
        )

    @staticmethod
    def get_all(
        db: Session
    ):
        return get_all_tasks(db)