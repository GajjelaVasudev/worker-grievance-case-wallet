"""REST API endpoints for Payment Discrepancy cases, evidence, timeline, and routing."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases import service
from app.core.config import Settings, get_settings
from app.core.database import ensure_default_worker, get_db
from app.models.case import EvidenceItem
from app.routing.matcher import match_verified_routes
from app.schemas.case import (
    AmendConfirmedCaseRequest,
    ConfirmCaseRequest,
    CreateCaseRequest,
    EvidenceCategory,
    TimelineMilestoneRequest,
    UpdateDraftRequest,
)
from app.storage.local_store import FileValidationError, LocalStorageService

router = APIRouter(prefix="/api/cases", tags=["cases"])

ALLOWED_UPLOAD_CATEGORIES: set[str] = {
    "payout_summary",
    "incentive_offer",
    "order_or_trip_records",
    "support_communication",
    "other",
}


def _storage(settings: Settings = Depends(get_settings)) -> LocalStorageService:
    return LocalStorageService(settings.evidence_storage_dir, settings.max_upload_mb)


def _worker_id(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> str:
    return ensure_default_worker(db, is_demo=settings.public_demo_mode)


@router.get("")
def list_wallet_cases(
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """List all grievance cases in the local worker wallet."""
    return {"cases": service.list_cases(db, worker_id)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CreateCaseRequest,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """Create a Payment/Incentive Discrepancy draft case."""
    try:
        case = service.create_payment_case(db, worker_id=worker_id, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"case": service.serialize_case(db, case)}


@router.get("/routing-preview")
def preview_verified_routing(
    platform: str,
    category: str = "payment_discrepancy",
    jurisdiction_state: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Query curated, verified routing channels or return 'Route requires verification'."""
    return match_verified_routes(
        db,
        platform=platform,
        category=category,
        jurisdiction_state=jurisdiction_state,
    )


@router.get("/{case_id}")
def get_case(
    case_id: str,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """Retrieve full details, checklist, routing, and timeline for a case."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return {"case": service.serialize_case(db, case)}


@router.patch("/{case_id}/draft")
def update_draft(
    case_id: str,
    payload: UpdateDraftRequest,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """Edit draft details prior to confirmation."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    try:
        updated = service.update_draft_case(db, case=case, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"case": service.serialize_case(db, updated)}


@router.post("/{case_id}/confirm")
def confirm_case(
    case_id: str,
    payload: ConfirmCaseRequest,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """Explicitly confirm reviewed details to transition draft -> confirmed."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    try:
        confirmed = service.confirm_case(db, case=case, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"case": service.serialize_case(db, confirmed)}


@router.patch("/{case_id}/confirm")
def amend_confirmed_case(
    case_id: str,
    payload: AmendConfirmedCaseRequest,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """Amend confirmed details while preserving prior history in the timeline."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    try:
        amended = service.amend_confirmed_case(db, case=case, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"case": service.serialize_case(db, amended)}


@router.post("/{case_id}/evidence", status_code=status.HTTP_201_CREATED)
async def upload_evidence(
    case_id: str,
    evidence_category: EvidenceCategory = Form(...),
    worker_note: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    storage: LocalStorageService = Depends(_storage),
) -> dict:
    """Upload validated screenshot/PDF evidence and refresh checklist."""
    if evidence_category not in ALLOWED_UPLOAD_CATEGORIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported evidence category")

    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    raw_bytes = await file.read()
    try:
        item = service.add_evidence_item(
            db,
            case=case,
            storage=storage,
            evidence_category=evidence_category,
            raw_bytes=raw_bytes,
            original_filename=file.filename,
            declared_content_type=file.content_type,
            worker_note=worker_note,
        )
    except FileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.refresh(case)
    return {
        "uploaded_evidence_id": item.id,
        "case": service.serialize_case(db, case),
    }


@router.get("/{case_id}/evidence/{evidence_id}/file")
def download_evidence_file(
    case_id: str,
    evidence_id: str,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    storage: LocalStorageService = Depends(_storage),
):
    """Serve an uploaded evidence file safely scoped to the worker's case."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    item = db.scalar(
        select(EvidenceItem).where(EvidenceItem.id == evidence_id, EvidenceItem.case_id == case.id)
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence item not found")

    try:
        file_path = storage.resolve_safe_path(item.storage_key)
    except FileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file missing")

    return FileResponse(
        path=file_path,
        media_type=item.mime_type,
        filename=item.original_filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{item.original_filename}"',
        },
    )


@router.delete("/{case_id}/evidence/{evidence_id}")
def delete_evidence(
    case_id: str,
    evidence_id: str,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    storage: LocalStorageService = Depends(_storage),
) -> dict:
    """Delete an uploaded evidence file and log the removal event."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    item = db.scalar(
        select(EvidenceItem).where(EvidenceItem.id == evidence_id, EvidenceItem.case_id == case.id)
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence item not found")

    service.delete_evidence_item(db, case=case, item=item, storage=storage)
    db.refresh(case)
    return {"case": service.serialize_case(db, case)}


@router.post("/{case_id}/timeline", status_code=status.HTTP_201_CREATED)
def append_timeline_milestone(
    case_id: str,
    payload: TimelineMilestoneRequest,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
) -> dict:
    """Append a milestone event to the immutable case timeline."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    try:
        event = service.append_milestone(db, case=case, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    db.refresh(case)
    return {"event_id": event.id, "case": service.serialize_case(db, case)}


@router.delete("/{case_id}")
def delete_case(
    case_id: str,
    db: Session = Depends(get_db),
    worker_id: str = Depends(_worker_id),
    storage: LocalStorageService = Depends(_storage),
) -> dict:
    """Delete a case and its uploaded evidence files from the local wallet."""
    case = service.get_case_or_none(db, case_id=case_id, worker_id=worker_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    service.delete_case_and_files(db, case=case, storage=storage)
    return {"deleted": True, "case_id": case_id}
