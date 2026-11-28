"""REST API endpoints for private audio upload, Sarvam transcription, and transcript editing."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.adapters.base import ASRProviderError
from app.cases import service
from app.core.config import Settings, get_settings
from app.core.database import ensure_default_worker, get_db
from app.schemas.voice import TranscribeRecordingRequest, UpdateTranscriptRequest
from app.storage.audio_validator import AudioValidationError
from app.storage.local_store import FileValidationError, LocalStorageService
from app.voice.storage import (
    delete_case_recording,
    get_recording_for_case,
    resolve_recording_audio_path,
    serialize_voice_recording,
    store_case_recording,
)
from app.voice.transcription import (
    ConsentRequiredError,
    DuplicateTranscriptionError,
    save_worker_transcript_edit,
    transcribe_case_recording,
)

router = APIRouter(prefix="/api/cases/{case_id}/voice", tags=["voice"])


def _storage(settings: Settings = Depends(get_settings)) -> LocalStorageService:
    return LocalStorageService(
        settings.evidence_storage_dir,
        settings.max_upload_mb,
        settings.max_audio_duration_seconds,
    )


def _worker_id(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> str:
    return ensure_default_worker(db, is_demo=settings.public_demo_mode)


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_voice_recording(
    case_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    storage: LocalStorageService = Depends(_storage),
) -> dict:
    """Validate and store a <=30s / <=10MB audio recording privately for a case."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    raw_bytes = await file.read()
    try:
        recording = store_case_recording(
            db,
            case=case,
            storage=storage,
            raw_bytes=raw_bytes,
            original_filename=file.filename,
            declared_content_type=file.content_type,
        )
    except (AudioValidationError, FileValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.refresh(case)
    return {
        "recording": serialize_voice_recording(recording),
        "case": service.serialize_case(db, case),
    }


@router.get("/{recording_id}/audio")
def stream_voice_recording(
    case_id: str,
    recording_id: str,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    storage: LocalStorageService = Depends(_storage),
):
    """Serve stored audio strictly scoped to the owning case."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    recording = get_recording_for_case(db, case_id=case.id, recording_id=recording_id)
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")

    try:
        audio_path = resolve_recording_audio_path(storage, case_id=case.id, recording=recording)
    except FileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not audio_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored audio file missing")

    return FileResponse(
        path=audio_path,
        media_type=recording.mime_type,
        filename=recording.original_filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{recording.original_filename}"',
        },
    )


@router.post("/{recording_id}/transcribe")
def transcribe_recording_endpoint(
    case_id: str,
    recording_id: str,
    payload: TranscribeRecordingRequest,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    settings: Settings = Depends(get_settings),
    storage: LocalStorageService = Depends(_storage),
):
    """Consent-gated Sarvam transcription with transparent keyless & error handling."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    recording = get_recording_for_case(db, case_id=case.id, recording_id=recording_id)
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")

    try:
        updated = transcribe_case_recording(
            db,
            case=case,
            recording=recording,
            storage=storage,
            settings=settings,
            consent_granted=payload.consent_granted,
            language_code=payload.language_code,
        )
    except ConsentRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DuplicateTranscriptionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ASRProviderError as exc:
        db.refresh(case)
        db.refresh(recording)
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error_code": exc.error_code,
                "detail": exc.user_message,
                "retryable": exc.retryable,
                "recording": serialize_voice_recording(recording),
            },
        )

    db.refresh(case)
    return {
        "recording": serialize_voice_recording(updated),
        "case": service.serialize_case(db, case),
    }


@router.patch("/{recording_id}/transcript")
def edit_recording_transcript(
    case_id: str,
    recording_id: str,
    payload: UpdateTranscriptRequest,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """Save worker-edited or manual transcript separately from immutable raw_transcript."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    recording = get_recording_for_case(db, case_id=case.id, recording_id=recording_id)
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")

    try:
        updated = save_worker_transcript_edit(
            db,
            case=case,
            recording=recording,
            edited_transcript=payload.worker_edited_transcript,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.refresh(case)
    return {
        "recording": serialize_voice_recording(updated),
        "case": service.serialize_case(db, case),
    }


@router.delete("/{recording_id}")
def delete_recording_endpoint(
    case_id: str,
    recording_id: str,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    storage: LocalStorageService = Depends(_storage),
) -> dict:
    """Delete one recording and its audio file while preserving sibling recordings & drafts."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    recording = get_recording_for_case(db, case_id=case.id, recording_id=recording_id)
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")

    delete_case_recording(db, case=case, recording=recording, storage=storage)
    db.refresh(case)
    return {"deleted": True, "recording_id": recording_id, "case": service.serialize_case(db, case)}
