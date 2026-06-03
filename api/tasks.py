from fastapi import APIRouter

from database.connection import SessionLocal
from schemas.task_schema import (
    TaskCreate,
    TaskResponse
)
from services.task_service import TaskService


router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"]
)


@router.post(
    "/",
    response_model=TaskResponse
)
def create_new_task(
    task: TaskCreate
):

    db = SessionLocal()

    try:

        return TaskService.create(
            db=db,
            description=task.description,
            priority=task.priority
        )

    finally:

        db.close()


@router.get(
    "/",
    response_model=list[TaskResponse]
)
def get_tasks():

    db = SessionLocal()

    try:

        return TaskService.get_all(db)

    finally:

        db.close()