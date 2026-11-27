"""Phase 2 ORM models: payment discrepancy cases, evidence, timeline, and curated routing."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GrievanceCase(Base):
    """Worker grievance case supporting explicit draft vs confirmed states."""

    __tablename__ = "grievance_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    worker_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("worker_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="payment_discrepancy")
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    jurisdiction_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    summary_title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    draft_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    field_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmed_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_confirmation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    evidence_items: Mapped[list["EvidenceItem"]] = relationship(
        back_populates="grievance_case",
        cascade="all, delete-orphan",
        order_by="EvidenceItem.uploaded_at.desc()",
    )
    timeline_events: Mapped[list["CaseTimelineEvent"]] = relationship(
        back_populates="grievance_case",
        cascade="all, delete-orphan",
        order_by="CaseTimelineEvent.created_at.asc()",
    )


class EvidenceItem(Base):
    """Uploaded supporting document/screenshot metadata and file checksum."""

    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("grievance_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_category: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    grievance_case: Mapped["GrievanceCase"] = relationship(back_populates="evidence_items")


class CaseTimelineEvent(Base):
    """Append-only chronological history of case milestones and amendments."""

    __tablename__ = "case_timeline_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("grievance_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_source: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    routing_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_evidence_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    snapshot_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    grievance_case: Mapped["GrievanceCase"] = relationship(back_populates="timeline_events")


class RoutingChannel(Base):
    """Curated, source-verified grievance routing registry."""

    __tablename__ = "routing_channels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    jurisdiction: Mapped[str] = mapped_column(String(64), nullable=False, default="national")
    tier: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(160), nullable=False)
    contact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_value: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    expected_sla_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="verified")
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    what_source_confirms: Mapped[str] = mapped_column(Text, nullable=False)
    last_verified_at: Mapped[str] = mapped_column(String(32), nullable=False)
    disclaimer_text: Mapped[str] = mapped_column(Text, nullable=False)
