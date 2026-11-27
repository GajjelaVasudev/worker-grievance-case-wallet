"""Database engine, session lifecycle, and single local wallet bootstrap."""

from collections.abc import Generator
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

DEFAULT_LOCAL_WALLET_ID = "00000000-0000-4000-8000-000000000001"


class Base(DeclarativeBase):
    """Declarative base for SQLAlchemy ORM models."""


def _build_engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)


engine = _build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a transactional database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_default_worker(db: Session, *, is_demo: bool = False) -> str:
    """Ensure the single local worker wallet exists for Round 2 zero-login flow."""
    from app.models.worker import WorkerProfile

    existing = db.scalar(select(WorkerProfile).where(WorkerProfile.id == DEFAULT_LOCAL_WALLET_ID))
    if existing is not None:
        return existing.id

    worker = WorkerProfile(
        id=DEFAULT_LOCAL_WALLET_ID or str(uuid.uuid4()),
        display_label="Demo Worker Wallet" if is_demo else "Local Worker Wallet",
        preferred_language="en-IN",
        auth_subject=None,
        is_demo=is_demo,
    )
    db.add(worker)
    db.commit()
    return worker.id
