"""Phase 3 ORM models: voice recordings and structured extraction drafts."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.case import GrievanceCase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VoiceRecording(Base):
    """Uploaded worker audio clip with immutable raw ASR transcript and separate worker edits."""

    __tablename__ = "voice_recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("grievance_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recording_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    codec_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    measured_duration_sec: Mapped[float] = mapped_column(Float, nullable=False)

    asr_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    asr_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asr_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transcription_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    detected_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language_probability: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    asr_transcribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_edited_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_demo_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sarvam_consent_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sarvam_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    grievance_case: Mapped["GrievanceCase"] = relationship(back_populates="voice_recordings")
    extraction_drafts: Mapped[list["CaseExtractionDraft"]] = relationship(
        back_populates="voice_recording",
        cascade="save-update, merge",
        passive_deletes=True,
    )


class CaseExtractionDraft(Base):
    """Structured extraction snapshot tied to its source transcript and quote provenance."""

    __tablename__ = "case_extraction_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("grievance_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    voice_recording_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("voice_recordings.id", ondelete="SET NULL"), nullable=True, index=True
    )

    source_transcript_layer: Mapped[str] = mapped_column(String(32), nullable=False)
    source_transcript_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

    llm_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    gemini_consent_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gemini_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    extracted_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    field_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stripped_ungrounded_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conflict_suggestions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    missing_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    grievance_case: Mapped["GrievanceCase"] = relationship(back_populates="extraction_drafts")
    voice_recording: Mapped["VoiceRecording | None"] = relationship(back_populates="extraction_drafts")
