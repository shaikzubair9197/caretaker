import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from database.connection import engine
from database.models import Base
from middleware.auth import verify_api_key
from api.tasks import router as task_router
from api.panic_dump import router as panic_router
from api.context import router as context_router
from api.agent import router as agent_router
from api.brain import router as brain_router
from api.memories import router as memory_router
from api.commitments import router as commitment_router
from api.llm_audit import router as llm_audit_router
from api.source_items import router as source_items_router
from api.health import router as health_router
from api.graph_sync import router as graph_sync_router
from utils.logger import get_logger

logger = get_logger("app")

MAX_REQUEST_BODY = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(100 * 1024)))  # 100 KB default


def _prewarm_embedder():
    """Load the sentence-transformers model in a background thread at startup."""
    try:
        from embeddings.embedder import encode
        encode("warmup")
        logger.info("Embedding model pre-warmed")
    except Exception as e:
        logger.warning(f"Embedding pre-warm failed (non-fatal): {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    threading.Thread(target=_prewarm_embedder, daemon=True).start()
    yield


app = FastAPI(title="Caretaker Agent", lifespan=lifespan)

# Allow the dashboard (any local origin) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_BODY:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Request body too large (max {MAX_REQUEST_BODY // 1024} KB)"},
        )
    return await call_next(request)


# ── Public endpoints ──────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "running", "service": "caretaker"}


# ── Protected API routes ──────────────────────────────────────────────────────
_auth = [Depends(verify_api_key)]

app.include_router(task_router, dependencies=_auth)
app.include_router(panic_router, dependencies=_auth)
app.include_router(context_router, dependencies=_auth)
app.include_router(agent_router, dependencies=_auth)
app.include_router(brain_router, dependencies=_auth)
app.include_router(memory_router, dependencies=_auth)
app.include_router(commitment_router, dependencies=_auth)
app.include_router(llm_audit_router, dependencies=_auth)
app.include_router(source_items_router, dependencies=_auth)
app.include_router(health_router, dependencies=_auth)
app.include_router(graph_sync_router, dependencies=_auth)

# ── Dashboard static files — served at /dashboard/ ───────────────────────────
_dashboard_dir = Path(__file__).parent / "dashboard"
if _dashboard_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")
