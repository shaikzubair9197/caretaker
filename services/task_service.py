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
        priority: str
    ):
        """
        Prevent duplicate tasks before creating new one
        """

        existing_task = (
            db.query(Task)
            .filter(Task.description == description)
            .first()
        )

        if existing_task:
            return existing_task

        return create_task(
            db,
            description,
            priority
        )

    @staticmethod
    def get_all(
        db: Session
    ):
        return get_all_tasks(db)