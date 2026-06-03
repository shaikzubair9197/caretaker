from fastapi import FastAPI

from database.connection import engine
from database.models import Base
from api.tasks import router as task_router
from api.panic_dump import router as panic_router
from api.context import router as telemetry_router
from api.context import router as context_router
from api.agent import (
    router as agent_router
)
from api.brain import router as brain_router

app = FastAPI(
    title="Caretaker Agent"
)

Base.metadata.create_all(
    bind=engine
)

app.include_router(task_router)
app.include_router(panic_router)
app.include_router(
    telemetry_router
)
app.include_router(context_router)
app.include_router(
    agent_router
)
app.include_router(brain_router)

@app.get("/")
def health_check():

    return {
        "status": "running",
        "service": "caretaker"
    }