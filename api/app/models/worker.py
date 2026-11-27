"""Worker profile ORM model (single local wallet for Round 2, auth-extensible)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WorkerProfile(Base):
    """Represents the worker wallet owner."""

    __tablename__ = "worker_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_label: Mapped[str] = mapped_column(String(120), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(32), nullable=False, default="en-IN")
    auth_subject: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
