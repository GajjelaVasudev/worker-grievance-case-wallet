"""Private audio recording storage, isolation, and safe deletion services."""

from datetime import datetime, timezone
from pathlib import Path
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cases.service import _append_timeline_event
from app.models.case import GrievanceCase
from app.models.voice import VoiceRecording
from app.storage.local_store import LocalStorageService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def serialize_voice_recording(recording: VoiceRecording) -> dict:
    """Serialize recording metadata without exposing internal storage keys or paths."""
    return {
        "id": recording.id,
        "case_id": recording.case_id,
        "recording_order": recording.recording_order,
        "original_filename": recording.original_filename,
        "mime_type": recording.mime_type,
        "codec_name": recording.codec_name,
        "size_bytes": recording.size_bytes,
        "sha256_hash": recording.sha256_hash,
        "measured_duration_sec": recording.measured_duration_sec,
        "asr_provider": recording.asr_provider,
        "asr_model": recording.asr_model,
        "asr_request_id": recording.asr_request_id,
        "transcription_status": recording.transcription_status,
        "detected_language": recording.detected_language,
        "language_probability": recording.language_probability,
        "raw_transcript": recording.raw_transcript,
        "asr_transcribed_at": recording.asr_transcribed_at.isoformat() if recording.asr_transcribed_at else None,
        "worker_edited_transcript": recording.worker_edited_transcript,
        "worker_edited_at": recording.worker_edited_at.isoformat() if recording.worker_edited_at else None,
        "is_demo_sample": recording.is_demo_sample,
        "sarvam_consent_granted": recording.sarvam_consent_granted,
        "sarvam_consented_at": recording.sarvam_consented_at.isoformat() if recording.sarvam_consented_at else None,
        "error_code": recording.error_code,
        "error_message": recording.error_message,
        "created_at": recording.created_at.isoformat(),
    }


def store_case_recording(
    db: Session,
    *,
    case: GrievanceCase,
    storage: LocalStorageService,
    raw_bytes: bytes,
    original_filename: str | None,
    declared_content_type: str | None = None,
) -> VoiceRecording:
    """Validate audio content/duration, store privately per case, and create VoiceRecording row."""
    recording_id = str(uuid.uuid4())
    max_order = (
        db.scalar(
            select(func.coalesce(func.max(VoiceRecording.recording_order), 0)).where(
                VoiceRecording.case_id == case.id
            )
        )
        or 0
    )

    stored = storage.save_audio_bytes(
        case_id=case.id,
        recording_id=recording_id,
        raw_bytes=raw_bytes,
        original_filename=original_filename,
        declared_content_type=declared_content_type,
    )

    now = _utcnow()
    recording = VoiceRecording(
        id=recording_id,
        case_id=case.id,
        recording_order=int(max_order) + 1,
        storage_key=stored.storage_key,
        original_filename=stored.sanitized_filename,
        mime_type=stored.mime_type,
        codec_name=stored.codec_name,
        size_bytes=stored.size_bytes,
        sha256_hash=stored.sha256_hash,
        measured_duration_sec=stored.duration_sec,
        asr_provider="none",
        transcription_status="pending",
        created_at=now,
    )
    try:
        db.add(recording)
        case.updated_at = now
        _append_timeline_event(
            db,
            case_id=case.id,
            event_type="voice_recorded",
            event_source="system",
            title=f"Saved voice recording #{recording.recording_order}",
            notes=f"{stored.sanitized_filename} ({stored.duration_sec:.1f}s)",
            snapshot_payload={
                "recording_id": recording_id,
                "recording_order": recording.recording_order,
                "duration_sec": stored.duration_sec,
                "sha256_hash": stored.sha256_hash,
            },
            occurred_at=now,
        )
        db.commit()
        db.refresh(recording)
        return recording
    except Exception:
        db.rollback()
        storage.delete_file(stored.storage_key)
        raise


def get_recording_for_case(db: Session, *, case_id: str, recording_id: str) -> VoiceRecording | None:
    """Fetch a VoiceRecording strictly scoped to its owning case."""
    return db.scalar(
        select(VoiceRecording).where(
            VoiceRecording.id == recording_id,
            VoiceRecording.case_id == case_id,
        )
    )


def resolve_recording_audio_path(
    storage: LocalStorageService,
    *,
    case_id: str,
    recording: VoiceRecording,
) -> Path:
    """Resolve the stored audio file path while enforcing case-level confinement."""
    return storage.resolve_case_scoped_path(case_id, recording.storage_key)


def delete_case_recording(
    db: Session,
    *,
    case: GrievanceCase,
    recording: VoiceRecording,
    storage: LocalStorageService,
) -> None:
    """Delete one recording and its stored file without disturbing other case data."""
    storage_key = recording.storage_key
    recording_id = recording.id
    recording_order = recording.recording_order
    filename = recording.original_filename

    storage.delete_file(storage_key)
    db.delete(recording)
    case.updated_at = _utcnow()
    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="voice_removed",
        event_source="system",
        title=f"Deleted voice recording #{recording_order}",
        notes=filename,
        snapshot_payload={"recording_id": recording_id, "recording_order": recording_order},
    )
    db.commit()
