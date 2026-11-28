"""Domain service for Payment Discrepancy cases, evidence, and append-only timeline."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cases.validation import (
    detect_missing_payment_fields,
    validate_confirmation_readiness,
    validate_field_provenance,
)
from app.evidence.checklist import evaluate_evidence_checklist
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase
from app.routing.matcher import match_verified_routes
from app.schemas.case import (
    AmendConfirmedCaseRequest,
    ConfirmCaseRequest,
    CreateCaseRequest,
    PaymentDiscrepancyDetails,
    TimelineMilestoneRequest,
    UpdateDraftRequest,
)
from app.storage.local_store import LocalStorageService

MILESTONE_TO_CASE_STATUS = {
    "worker_reported_submission": "submission_reported",
    "evidence_backed_submission": "submission_with_evidence",
    "platform_acknowledgement_reported": "acknowledgement_reported",
    "platform_response_reported": "response_reported",
    "escalation_reported": "escalation_reported",
    "closed": "closed",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_case_code(db: Session) -> str:
    existing_count = db.scalar(select(func.count(GrievanceCase.id))) or 0
    return f"CASE-{existing_count + 1:04d}-{uuid.uuid4().hex[:4].upper()}"


def _append_timeline_event(
    db: Session,
    *,
    case_id: str,
    event_type: str,
    event_source: str,
    title: str,
    notes: str | None = None,
    external_reference_id: str | None = None,
    routing_channel_id: str | None = None,
    linked_evidence_ids: list[str] | None = None,
    snapshot_payload: dict | None = None,
    occurred_at: datetime | None = None,
) -> CaseTimelineEvent:
    now = _utcnow()
    event = CaseTimelineEvent(
        id=str(uuid.uuid4()),
        case_id=case_id,
        event_type=event_type,
        event_source=event_source,
        title=title,
        notes=notes,
        external_reference_id=external_reference_id,
        routing_channel_id=routing_channel_id,
        linked_evidence_ids=linked_evidence_ids,
        snapshot_payload=snapshot_payload,
        occurred_at=occurred_at or now,
        created_at=now,
    )
    db.add(event)
    return event


def _effective_details_model(case: GrievanceCase) -> PaymentDiscrepancyDetails:
    raw = case.confirmed_details if case.confirmed_details is not None else (case.draft_details or {})
    return PaymentDiscrepancyDetails.model_validate(raw)


def serialize_case(db: Session, case: GrievanceCase) -> dict:
    """Build complete API representation for a Payment Discrepancy case."""
    from app.voice.extraction import serialize_extraction_draft
    from app.voice.storage import serialize_voice_recording

    active_details = _effective_details_model(case)
    active_dict = active_details.model_dump()
    missing_fields = detect_missing_payment_fields(active_details)
    # Surface unresolved grounding/clarification requirements from extraction drafts
    seen_clarifications = set()
    for draft in reversed(case.extraction_drafts):
        for field_name, claim in (draft.stripped_ungrounded_fields or {}).items():
            if field_name in seen_clarifications:
                continue
            if field_name in active_dict and active_dict[field_name] is not None:
                continue
            seen_clarifications.add(field_name)
            missing_fields.insert(
                0,
                {
                    "field": field_name,
                    "importance": "clarification_required",
                    "reason": claim.get("reason"),
                    "prompt": (
                        f"Clarification required for '{field_name}': {claim.get('reason')} "
                        "Please review and enter the correct value if known."
                    ),
                },
            )
    checklist = evaluate_evidence_checklist(case.category, list(case.evidence_items))
    routing = match_verified_routes(
        db,
        platform=case.platform,
        category=case.category,
        jurisdiction_state=case.jurisdiction_state,
    )

    return {
        "id": case.id,
        "case_code": case.case_code,
        "worker_id": case.worker_id,
        "category": case.category,
        "platform": case.platform,
        "jurisdiction_state": case.jurisdiction_state,
        "status": case.status,
        "summary_title": case.summary_title,
        "draft_details": case.draft_details,
        "field_provenance": case.field_provenance,
        "confirmed_details": case.confirmed_details,
        "confirmed_at": case.confirmed_at.isoformat() if case.confirmed_at else None,
        "worker_confirmation_note": case.worker_confirmation_note,
        "missing_fields": missing_fields,
        "evidence_items": [
            {
                "id": item.id,
                "case_id": item.case_id,
                "evidence_category": item.evidence_category,
                "original_filename": item.original_filename,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "sha256_hash": item.sha256_hash,
                "worker_note": item.worker_note,
                "uploaded_at": item.uploaded_at.isoformat(),
            }
            for item in case.evidence_items
        ],
        "evidence_checklist": checklist,
        "routing": routing,
        "voice_recordings": [serialize_voice_recording(rec) for rec in case.voice_recordings],
        "extraction_drafts": [serialize_extraction_draft(d) for d in case.extraction_drafts],
        "timeline": [
            {
                "id": ev.id,
                "event_type": ev.event_type,
                "event_source": ev.event_source,
                "title": ev.title,
                "notes": ev.notes,
                "external_reference_id": ev.external_reference_id,
                "routing_channel_id": ev.routing_channel_id,
                "linked_evidence_ids": ev.linked_evidence_ids,
                "snapshot_payload": ev.snapshot_payload,
                "occurred_at": ev.occurred_at.isoformat(),
                "created_at": ev.created_at.isoformat(),
            }
            for ev in case.timeline_events
        ],
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
    }


def list_cases(db: Session, worker_id: str) -> list[dict]:
    """List cases belonging to the local worker wallet."""
    rows = list(
        db.scalars(
            select(GrievanceCase)
            .where(GrievanceCase.worker_id == worker_id)
            .order_by(GrievanceCase.created_at.desc())
        )
    )
    return [serialize_case(db, row) for row in rows]


def get_case_or_none(db: Session, *, case_id: str, worker_id: str) -> GrievanceCase | None:
    """Fetch a single case scoped to the active worker."""
    return db.scalar(
        select(GrievanceCase).where(
            GrievanceCase.id == case_id,
            GrievanceCase.worker_id == worker_id,
        )
    )


def create_payment_case(db: Session, *, worker_id: str, payload: CreateCaseRequest) -> GrievanceCase:
    """Create a Payment Discrepancy case in draft status with an initial timeline event."""
    if payload.category != "payment_discrepancy":
        raise ValueError(
            "Only 'payment_discrepancy' is supported in Phase 2. Other categories will be added after the hero workflow."
        )

    provenance = validate_field_provenance(payload.field_provenance, payload.transcript_context)
    details_dict = payload.details.model_dump()
    platform = payload.platform or payload.details.platform or "other"
    if details_dict.get("platform") is None and payload.platform is not None:
        details_dict["platform"] = payload.platform

    case_id = str(uuid.uuid4())
    case = GrievanceCase(
        id=case_id,
        worker_id=worker_id,
        case_code=_generate_case_code(db),
        category="payment_discrepancy",
        platform=platform,
        jurisdiction_state=payload.jurisdiction_state,
        status="draft",
        summary_title=payload.summary_title,
        draft_details=details_dict,
        field_provenance=provenance,
        confirmed_details=None,
        confirmed_at=None,
    )
    db.add(case)
    db.flush()

    _append_timeline_event(
        db,
        case_id=case_id,
        event_type="case_created",
        event_source="system",
        title="Draft payment discrepancy case created",
        notes=f"Platform: {platform}",
        snapshot_payload={"status": "draft", "draft_details": details_dict},
    )
    db.commit()
    db.refresh(case)
    return case


def update_draft_case(db: Session, *, case: GrievanceCase, payload: UpdateDraftRequest) -> GrievanceCase:
    """Update draft details before confirmation and record a draft_updated timeline event."""
    if case.status != "draft":
        raise ValueError("Confirmed cases cannot be edited via draft endpoint; use confirmed amendment instead.")

    provenance = validate_field_provenance(payload.field_provenance, payload.transcript_context)
    details_dict = payload.details.model_dump()
    platform = payload.platform or payload.details.platform or case.platform
    if details_dict.get("platform") is None and platform != "other":
        details_dict["platform"] = platform

    case.platform = platform
    if payload.jurisdiction_state is not None:
        case.jurisdiction_state = payload.jurisdiction_state
    if payload.summary_title is not None:
        case.summary_title = payload.summary_title
    case.draft_details = details_dict
    if provenance is not None:
        case.field_provenance = provenance
    case.updated_at = _utcnow()

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="draft_updated",
        event_source="system",
        title="Draft case details updated",
        snapshot_payload={"draft_details": details_dict},
    )
    db.commit()
    db.refresh(case)
    return case


def confirm_case(db: Session, *, case: GrievanceCase, payload: ConfirmCaseRequest) -> GrievanceCase:
    """Require explicit worker confirmation before transitioning draft -> confirmed."""
    if not payload.confirm:
        raise ValueError("Explicit worker confirmation (confirm=true) is required before confirming a case.")
    if case.status != "draft":
        raise ValueError(f"Case is already '{case.status}' and cannot be confirmed again.")

    details_model = payload.details
    if details_model.platform is None and (payload.platform or case.platform != "other"):
        details_model = details_model.model_copy(update={"platform": payload.platform or case.platform})

    validate_confirmation_readiness(details_model)

    confirmed_dict = details_model.model_dump()
    now = _utcnow()
    case.platform = confirmed_dict["platform"] or case.platform
    if payload.jurisdiction_state is not None:
        case.jurisdiction_state = payload.jurisdiction_state
    if payload.summary_title is not None:
        case.summary_title = payload.summary_title
    case.confirmed_details = confirmed_dict
    case.confirmed_at = now
    case.worker_confirmation_note = payload.worker_confirmation_note
    case.status = "confirmed"
    case.updated_at = now

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="case_confirmed",
        event_source="system",
        title="Worker reviewed and confirmed case details",
        notes=payload.worker_confirmation_note,
        snapshot_payload={"confirmed_details": confirmed_dict},
        occurred_at=now,
    )
    db.commit()
    db.refresh(case)
    return case


def amend_confirmed_case(
    db: Session, *, case: GrievanceCase, payload: AmendConfirmedCaseRequest
) -> GrievanceCase:
    """Amend confirmed details while preserving prior history in append-only timeline."""
    if case.status == "draft" or case.confirmed_details is None:
        raise ValueError("Case must be confirmed before amendments can be logged.")

    details_model = payload.details
    if details_model.platform is None and (payload.platform or case.platform != "other"):
        details_model = details_model.model_copy(update={"platform": payload.platform or case.platform})
    validate_confirmation_readiness(details_model)

    previous_snapshot = dict(case.confirmed_details)
    new_snapshot = details_model.model_dump()
    now = _utcnow()

    case.platform = new_snapshot["platform"] or case.platform
    if payload.jurisdiction_state is not None:
        case.jurisdiction_state = payload.jurisdiction_state
    if payload.summary_title is not None:
        case.summary_title = payload.summary_title
    case.confirmed_details = new_snapshot
    case.updated_at = now

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="details_amended",
        event_source="system",
        title="Confirmed case details amended by worker",
        notes=payload.amendment_reason,
        snapshot_payload={"previous": previous_snapshot, "updated": new_snapshot},
        occurred_at=now,
    )
    db.commit()
    db.refresh(case)
    return case


def add_evidence_item(
    db: Session,
    *,
    case: GrievanceCase,
    storage: LocalStorageService,
    evidence_category: str,
    raw_bytes: bytes,
    original_filename: str | None,
    declared_content_type: str | None,
    worker_note: str | None,
) -> EvidenceItem:
    """Validate, persist, and log a newly uploaded evidence file."""
    evidence_id = str(uuid.uuid4())
    stored = storage.save_evidence_bytes(
        case_id=case.id,
        evidence_id=evidence_id,
        raw_bytes=raw_bytes,
        original_filename=original_filename,
        declared_content_type=declared_content_type,
    )
    now = _utcnow()
    item = EvidenceItem(
        id=evidence_id,
        case_id=case.id,
        evidence_category=evidence_category,
        storage_key=stored.storage_key,
        original_filename=stored.sanitized_filename,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256_hash=stored.sha256_hash,
        worker_note=worker_note.strip() if worker_note and worker_note.strip() else None,
        uploaded_at=now,
    )
    db.add(item)
    case.updated_at = now

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="evidence_added",
        event_source="system",
        title=f"Uploaded evidence ({evidence_category})",
        notes=stored.sanitized_filename,
        linked_evidence_ids=[evidence_id],
        snapshot_payload={
            "evidence_id": evidence_id,
            "evidence_category": evidence_category,
            "sha256_hash": stored.sha256_hash,
        },
        occurred_at=now,
    )
    db.commit()
    db.refresh(item)
    return item


def delete_evidence_item(
    db: Session, *, case: GrievanceCase, item: EvidenceItem, storage: LocalStorageService
) -> None:
    """Remove an evidence item and record the deletion event on the timeline."""
    storage.delete_file(item.storage_key)
    filename = item.original_filename
    category = item.evidence_category
    item_id = item.id
    db.delete(item)
    case.updated_at = _utcnow()

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="evidence_removed",
        event_source="system",
        title=f"Deleted evidence ({category})",
        notes=filename,
        snapshot_payload={"evidence_id": item_id, "evidence_category": category},
    )
    db.commit()


def append_milestone(db: Session, *, case: GrievanceCase, payload: TimelineMilestoneRequest) -> CaseTimelineEvent:
    """Append a worker-reported milestone without mutating prior history or implying platform receipt."""
    if payload.event_type in MILESTONE_TO_CASE_STATUS:
        if case.status == "draft":
            raise ValueError("Confirm the case before logging external submission or escalation milestones.")

    valid_case_evidence_ids = {item.id for item in case.evidence_items}
    linked_ids = payload.linked_evidence_ids or []
    unknown_ids = [eid for eid in linked_ids if eid not in valid_case_evidence_ids]
    if unknown_ids:
        raise ValueError("All linked_evidence_ids must belong to uploaded evidence items on this case.")

    if payload.event_type == "evidence_backed_submission" and len(linked_ids) == 0:
        raise ValueError(
            "Submission supported by recorded evidence requires at least one uploaded evidence item linked to the event."
        )

    if payload.event_type in MILESTONE_TO_CASE_STATUS:
        case.status = MILESTONE_TO_CASE_STATUS[payload.event_type]

    now = _utcnow()
    case.updated_at = now
    event = _append_timeline_event(
        db,
        case_id=case.id,
        event_type=payload.event_type,
        event_source="worker",
        title=payload.title,
        notes=payload.notes,
        external_reference_id=payload.external_reference_id,
        routing_channel_id=payload.routing_channel_id,
        linked_evidence_ids=linked_ids or None,
        snapshot_payload={
            "case_status": case.status,
            "platform_receipt_confirmed": False,
        },
        occurred_at=payload.occurred_at or now,
    )
    db.commit()
    db.refresh(event)
    return event


def delete_case_and_files(db: Session, *, case: GrievanceCase, storage: LocalStorageService) -> None:
    """Delete a case and all stored evidence and voice recording files on disk."""
    for item in case.evidence_items:
        storage.delete_file(item.storage_key)
    for rec in case.voice_recordings:
        storage.delete_file(rec.storage_key)
    db.delete(case)
    db.commit()
