from fastapi import APIRouter

from database.connection import SessionLocal
from services.brain_service import BrainService


router = APIRouter(
    prefix="/brain",
    tags=["Brain"]
)


@router.get("/")
def get_brain():

    db = SessionLocal()

    try:
        return BrainService.build_context(db)

    finally:
        db.close()