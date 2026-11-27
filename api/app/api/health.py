"""Healthcheck and runtime capability endpoints."""

from pathlib import Path

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.capabilities import build_capabilities_payload
from app.core.config import Settings, get_settings
from app.core.database import DEFAULT_LOCAL_WALLET_ID, get_db

router = APIRouter(prefix="/api", tags=["system"])


def _check_storage_writable(storage_dir: str) -> bool:
    """Verify the configured evidence storage directory exists and is writable."""
    try:
        target = Path(storage_dir)
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".healthcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


@router.get("/health")
def health_check(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Verify PostgreSQL connectivity and local evidence storage writability."""
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    storage_ok = _check_storage_writable(settings.evidence_storage_dir)
    healthy = db_ok and storage_ok

    payload = {
        "status": "ok" if healthy else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "storage": "ok" if storage_ok else "unavailable",
        "local_wallet_id": DEFAULT_LOCAL_WALLET_ID,
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )


@router.get("/capabilities")
def get_capabilities(settings: Settings = Depends(get_settings)) -> dict:
    """Expose active ASR/LLM provider readiness without secrets."""
    return build_capabilities_payload(settings)
