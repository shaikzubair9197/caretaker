from fastapi import APIRouter

from database.connection import SessionLocal

from schemas.panic_schema import (
    PanicDumpRequest,
    PanicDumpResponse
)

from services.task_service import TaskService
from services.memory_service import MemoryService  # <-- NEW (next layer)


router = APIRouter(
    prefix="/panic_dump",
    tags=["Panic Dump"]
)


@router.post(
    "/",
    response_model=PanicDumpResponse
)
def panic_dump(request: PanicDumpRequest):

    db = SessionLocal()

    try:

        # ----------------------------
        # 1. CREATE TASK (short-term action)
        # ----------------------------
        TaskService.create(
            db=db,
            description=request.text,
            priority="medium"
        )

        # ----------------------------
        # 2. STORE MEMORY (long-term brain)
        # ----------------------------
        MemoryService.create(
            db=db,
            text=request.text,
            memory_type="panic_note",
            importance=2
        )

        return PanicDumpResponse(
            intent="capture",
            action="task_and_memory_created",
            details="Task + Memory saved successfully"
        )

    finally:
        db.close()