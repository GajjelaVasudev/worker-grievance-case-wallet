"""FastAPI entrypoint for Worker Grievance Case Wallet."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.cases import router as cases_router
from app.api.health import router as health_router
from app.api.voice import router as voice_router
from app.core.config import get_settings
from app.core.database import SessionLocal, ensure_default_worker
from app.routing.matcher import ensure_routing_seed


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize evidence storage, local wallet, and verified routing registry."""
    settings = get_settings()
    Path(settings.evidence_storage_dir).mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        ensure_default_worker(db, is_demo=settings.public_demo_mode)
        ensure_routing_seed(db)
    except Exception:
        # Never crash container startup before migrations/healthchecks inspect DB state.
        db.rollback()
    finally:
        db.close()
    yield


app = FastAPI(
    title="Worker Grievance Case Wallet API",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(cases_router)
app.include_router(voice_router)
