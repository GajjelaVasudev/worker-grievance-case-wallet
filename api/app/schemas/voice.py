"""Pydantic schemas for voice recording upload, Sarvam transcription, transcript edits, and extraction."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TranscribeRecordingRequest(BaseModel):
    """Explicit consent payload required before calling Sarvam STT."""

    consent_granted: bool
    language_code: str | None = None

    @field_validator("language_code", mode="before")
    @classmethod
    def clean_language_code(cls, value: str | None) -> str | None:
        """Normalize empty strings to null."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class UpdateTranscriptRequest(BaseModel):
    """Separate worker-edited or manual transcript payload."""

    worker_edited_transcript: str = Field(min_length=1, max_length=10000)

    @field_validator("worker_edited_transcript", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """Trim surrounding whitespace before validation."""
        return (value or "").strip()


class ExtractDraftRequest(BaseModel):
    """Explicit consent and worker-approval payload required before calling Gemini extraction."""

    consent_granted: bool
    transcript_approved_by_worker: bool
    transcript_layer: Literal["worker_edited_transcript", "raw_transcript"] = "worker_edited_transcript"
