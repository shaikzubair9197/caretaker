from fastapi import APIRouter

from database.connection import SessionLocal

from schemas.agent_schema import (
    AgentRequest
)

from services.agent_service import (
    process_message
)

router = APIRouter(
    prefix="/agent",
    tags=["Agent"]
)


@router.post("/chat")
def chat(
    request: AgentRequest
):

    db = SessionLocal()

    try:

        return process_message(
            db,
            request.message
        )

    finally:

        db.close()