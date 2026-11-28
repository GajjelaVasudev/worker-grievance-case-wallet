"""Consent-gated Sarvam speech-to-text workflow and separate worker transcript editing."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapters.base import ASRProvider, ASRProviderError
from app.adapters.sarvam_asr import SarvamASRProvider
from app.cases.service import _append_timeline_event
from app.core.config import Settings
from app.models.case import GrievanceCase
from app.models.voice import VoiceRecording
from app.storage.local_store import LocalStorageService
from app.voice.storage import resolve_recording_audio_path


class ConsentRequiredError(ValueError):
    """Raised when external ASR is requested without explicit worker consent."""


class DuplicateTranscriptionError(ValueError):
    """Raised when a completed original ASR transcript already exists for a recording."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def transcribe_case_recording(
    db: Session,
    *,
    case: GrievanceCase,
    recording: VoiceRecording,
    storage: LocalStorageService,
    settings: Settings,
    consent_granted: bool,
    language_code: str | None = None,
    asr_provider: ASRProvider | None = None,
) -> VoiceRecording:
    """Transcribe a stored recording via Sarvam once, preserving immutable raw_transcript."""
    if not consent_granted:
        raise ConsentRequiredError(
            "Explicit worker consent is required before sending audio to Sarvam."
        )

    if recording.case_id != case.id:
        raise ValueError("Recording does not belong to this grievance case.")

    # Never overwrite an already-completed original ASR transcript or double-bill credits.
    if recording.raw_transcript is not None or recording.transcription_status == "completed":
        raise DuplicateTranscriptionError(
            "Original ASR transcript already exists for this recording and cannot be overwritten."
        )

    provider = asr_provider or SarvamASRProvider(settings)
    capability = provider.capability()
    now = _utcnow()

    if not capability.configured:
        recording.sarvam_consent_granted = True
        recording.sarvam_consented_at = now
        recording.asr_provider = "none"
        recording.asr_model = None
        recording.transcription_status = "key_not_set"
        recording.error_code = "key_not_set"
        recording.error_message = (
            "Sarvam API key not set. Audio recording is saved; enter a manual transcript or wait for provider configuration."
        )
        case.updated_at = now
        db.commit()
        db.refresh(recording)
        return recording

    audio_path = resolve_recording_audio_path(storage, case_id=case.id, recording=recording)
    if not audio_path.exists() or not audio_path.is_file():
        raise FileNotFoundError("Stored audio recording file is missing from disk.")
    audio_bytes = audio_path.read_bytes()

    recording.sarvam_consent_granted = True
    recording.sarvam_consented_at = now
    db.flush()

    try:
        result = provider.transcribe_audio(
            audio_bytes=audio_bytes,
            filename=recording.original_filename,
            mime_type=recording.mime_type,
            language_code=language_code,
        )
    except ASRProviderError as exc:
        # Preserve any existing state safely; never mark completed on failure.
        recording.asr_provider = provider.name
        recording.asr_model = capability.model
        recording.transcription_status = "failed"
        recording.error_code = exc.error_code
        recording.error_message = exc.user_message
        case.updated_at = now
        _append_timeline_event(
            db,
            case_id=case.id,
            event_type="asr_transcription_failed",
            event_source="system",
            title=f"Transcription failed for recording #{recording.recording_order}",
            notes=f"{exc.error_code}: {exc.user_message}",
            snapshot_payload={
                "recording_id": recording.id,
                "error_code": exc.error_code,
            },
            occurred_at=now,
        )
        db.commit()
        db.refresh(recording)
        raise

    # Write-once assignment of original ASR transcript.
    recording.asr_provider = result.provider
    recording.asr_model = result.model
    recording.asr_request_id = result.request_id
    recording.detected_language = result.language_code
    recording.language_probability = result.language_probability
    recording.raw_transcript = result.transcript
    recording.asr_transcribed_at = now
    recording.transcription_status = "completed"
    recording.error_code = None
    recording.error_message = None
    case.updated_at = now

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="asr_transcribed",
        event_source="system",
        title=f"Transcribed recording #{recording.recording_order} via {result.provider} ({result.model})",
        notes=f"Language: {result.language_code or 'unknown'}",
        snapshot_payload={
            "recording_id": recording.id,
            "asr_provider": result.provider,
            "asr_model": result.model,
            "asr_request_id": result.request_id,
            "language_code": result.language_code,
        },
        occurred_at=now,
    )
    db.commit()
    db.refresh(recording)
    return recording


def save_worker_transcript_edit(
    db: Session,
    *,
    case: GrievanceCase,
    recording: VoiceRecording,
    edited_transcript: str,
) -> VoiceRecording:
    """Save worker-edited or manual transcript separately without altering raw_transcript."""
    if recording.case_id != case.id:
        raise ValueError("Recording does not belong to this grievance case.")

    cleaned = (edited_transcript or "").strip()
    if not cleaned:
        raise ValueError("Transcript text cannot be empty.")

    now = _utcnow()
    # Explicitly do NOT touch raw_transcript, asr_provider, asr_model, or asr_request_id.
    recording.worker_edited_transcript = cleaned
    recording.worker_edited_at = now
    case.updated_at = now

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="transcript_edited",
        event_source="worker",
        title=f"Worker saved transcript text for recording #{recording.recording_order}",
        notes="Original ASR transcript preserved separately." if recording.raw_transcript else "Manual worker transcript entry.",
        snapshot_payload={
            "recording_id": recording.id,
            "has_immutable_raw_asr": recording.raw_transcript is not None,
            "edited_transcript_length": len(cleaned),
        },
        occurred_at=now,
    )
    db.commit()
    db.refresh(recording)
    return recording
