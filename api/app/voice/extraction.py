"""Consent-gated Gemini structured extraction, exact quote provenance, and multi-recording merge."""

from datetime import datetime, timezone
import uuid

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.adapters.base import LLMProvider, LLMProviderError
from app.adapters.gemini_llm import GeminiLLMProvider
from app.cases.service import _append_timeline_event
from app.cases.validation import detect_missing_payment_fields
from app.core.config import Settings
from app.models.case import GrievanceCase
from app.models.voice import CaseExtractionDraft, VoiceRecording
from app.schemas.case import PaymentDiscrepancyDetails
from app.voice.transcription import ConsentRequiredError


class TranscriptNotApprovedError(ValueError):
    """Raised when extraction is attempted without explicit worker approval of the transcript."""


# Mapping from Gemini structured output keys to PaymentDiscrepancyDetails fields
_GEMINI_TO_DETAIL_FIELD: dict[str, str] = {
    "platform": "platform",
    "short_issue_summary": "issue_description",
    "disputed_amount": "discrepancy_amount_inr",
    "currency": "currency",
    "relevant_period": "payout_cycle_raw",
    "work_completed": "work_completed_summary",
    "expected_payment_amount": "expected_amount_inr",
    "actual_payment_amount": "received_amount_inr",
    "incentive_scheme_name": "incentive_scheme_name",
    "work_city": "work_city",
    "prior_ticket_id": "prior_ticket_id",
    "previous_support_contacts": "support_contact_summary",
    "support_responses": "support_response_summary",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def serialize_extraction_draft(draft: CaseExtractionDraft) -> dict:
    """Serialize a CaseExtractionDraft snapshot for review UI and API responses."""
    return {
        "id": draft.id,
        "case_id": draft.case_id,
        "voice_recording_id": draft.voice_recording_id,
        "source_transcript_layer": draft.source_transcript_layer,
        "source_transcript_snapshot": draft.source_transcript_snapshot,
        "llm_provider": draft.llm_provider,
        "llm_model": draft.llm_model,
        "extraction_status": draft.extraction_status,
        "gemini_consent_granted": draft.gemini_consent_granted,
        "gemini_consented_at": draft.gemini_consented_at.isoformat() if draft.gemini_consented_at else None,
        "extracted_fields": draft.extracted_fields,
        "field_provenance": draft.field_provenance,
        "stripped_ungrounded_fields": draft.stripped_ungrounded_fields,
        "conflict_suggestions": draft.conflict_suggestions,
        "missing_fields": draft.missing_fields,
        "error_message": draft.error_message,
        "created_at": draft.created_at.isoformat(),
    }


def validate_and_ground_extraction(
    *,
    raw_payload: dict,
    approved_transcript: str,
    recording_id: str,
    transcript_layer: str,
) -> tuple[PaymentDiscrepancyDetails, dict[str, dict], dict[str, dict], dict]:
    """Verify exact verbatim transcript quotations and reject unsupported/invalid claims."""
    normalized_transcript = _normalize_whitespace(approved_transcript)
    candidate_details: dict[str, object] = {}
    grounded_provenance: dict[str, dict] = {}
    stripped_claims: dict[str, dict] = {}

    def _check_entry(gemini_key: str, target_field: str) -> object:
        entry = raw_payload.get(gemini_key)
        if not isinstance(entry, dict):
            stripped_claims[target_field] = {
                "rejected_value": entry,
                "rejected_quote": None,
                "reason": "Malformed extraction entry (expected {value, source_quote} object).",
            }
            return None

        val = entry.get("value")
        quote = entry.get("source_quote")
        if val is None or (isinstance(val, str) and not val.strip()):
            return None

        if not isinstance(quote, str) or not quote.strip():
            stripped_claims[target_field] = {
                "rejected_value": val,
                "rejected_quote": quote,
                "reason": "Missing source_quote for extracted value.",
            }
            return None

        normalized_quote = _normalize_whitespace(quote)
        if normalized_quote not in normalized_transcript:
            stripped_claims[target_field] = {
                "rejected_value": val,
                "rejected_quote": quote,
                "reason": "Unsupported or fabricated quotation not found verbatim in approved transcript.",
            }
            return None

        # Validate individual field against PaymentDiscrepancyDetails constraints when mapped.
        if target_field in PaymentDiscrepancyDetails.model_fields:
            try:
                validated_model = PaymentDiscrepancyDetails.model_validate({target_field: val})
                validated_val = getattr(validated_model, target_field)
            except ValidationError as exc:
                stripped_claims[target_field] = {
                    "rejected_value": val,
                    "rejected_quote": quote,
                    "reason": f"Schema validation rejected value: {exc.errors()[0]['msg']}",
                }
                return None
        else:
            validated_val = val

        if validated_val is None:
            return None

        grounded_provenance[target_field] = {
            "origin": "llm_extracted",
            "value": validated_val,
            "voice_recording_id": recording_id,
            "transcript_layer": transcript_layer,
            "source_quote": quote.strip(),
        }
        return validated_val

    # Validate grievance_category separately (must match payment_discrepancy if non-null)
    cat_val = _check_entry("grievance_category", "grievance_category")
    if cat_val not in (None, "payment_discrepancy"):
        stripped_claims["grievance_category"] = {
            "rejected_value": cat_val,
            "rejected_quote": grounded_provenance.pop("grievance_category", {}).get("source_quote"),
            "reason": "Unsupported grievance category in Phase 3.",
        }

    for gemini_key, detail_field in _GEMINI_TO_DETAIL_FIELD.items():
        validated_val = _check_entry(gemini_key, detail_field)
        if validated_val is not None:
            candidate_details[detail_field] = validated_val

    details_model = PaymentDiscrepancyDetails.model_validate(candidate_details)
    raw_missing = raw_payload.get("missing_information")
    raw_followups = raw_payload.get("suggested_followup_questions")
    llm_gaps = {
        "missing_information": [
            str(x).strip() for x in raw_missing if isinstance(x, str) and x.strip()
        ]
        if isinstance(raw_missing, list)
        else [],
        "suggested_followup_questions": [
            str(x).strip() for x in raw_followups if isinstance(x, str) and x.strip()
        ]
        if isinstance(raw_followups, list)
        else [],
    }
    return details_model, grounded_provenance, stripped_claims, llm_gaps


def extract_draft_from_recording(
    db: Session,
    *,
    case: GrievanceCase,
    recording: VoiceRecording,
    settings: Settings,
    consent_granted: bool,
    transcript_approved_by_worker: bool,
    transcript_layer: str = "worker_edited_transcript",
    llm_provider: LLMProvider | None = None,
) -> CaseExtractionDraft:
    """Extract grounded Payment Discrepancy facts from a worker-approved transcript."""
    if not consent_granted:
        raise ConsentRequiredError(
            "Explicit worker consent is required before sending transcript text to Gemini."
        )
    if not transcript_approved_by_worker:
        raise TranscriptNotApprovedError(
            "Worker must review and approve the selected transcript before structured extraction."
        )
    if recording.case_id != case.id:
        raise ValueError("Recording does not belong to this grievance case.")

    if transcript_layer == "worker_edited_transcript":
        approved_text = recording.worker_edited_transcript
    elif transcript_layer == "raw_transcript":
        approved_text = recording.raw_transcript
    else:
        raise ValueError(f"Unsupported transcript_layer '{transcript_layer}'.")

    if not approved_text or not approved_text.strip():
        raise ValueError(
            f"Selected transcript layer '{transcript_layer}' is empty. Save or transcribe text first."
        )

    provider = llm_provider or GeminiLLMProvider(settings)
    capability = provider.capability()
    now = _utcnow()
    draft_id = str(uuid.uuid4())

    current_draft_model = PaymentDiscrepancyDetails.model_validate(case.draft_details or {})
    deterministic_missing = detect_missing_payment_fields(current_draft_model)

    if not capability.configured:
        draft = CaseExtractionDraft(
            id=draft_id,
            case_id=case.id,
            voice_recording_id=recording.id,
            source_transcript_layer=transcript_layer,
            source_transcript_snapshot=approved_text,
            llm_provider="none",
            llm_model=None,
            extraction_status="key_not_set",
            gemini_consent_granted=True,
            gemini_consented_at=now,
            extracted_fields=None,
            field_provenance=None,
            stripped_ungrounded_fields=None,
            conflict_suggestions=None,
            missing_fields=deterministic_missing,
            error_message=(
                "Gemini API key not set. Manual review and field entry remain available without AI extraction."
            ),
            created_at=now,
        )
        db.add(draft)
        case.updated_at = now
        db.commit()
        db.refresh(draft)
        return draft

    try:
        result = provider.extract_payment_discrepancy(approved_transcript=approved_text)
    except LLMProviderError as exc:
        failed_draft = CaseExtractionDraft(
            id=draft_id,
            case_id=case.id,
            voice_recording_id=recording.id,
            source_transcript_layer=transcript_layer,
            source_transcript_snapshot=approved_text,
            llm_provider=provider.name,
            llm_model=capability.model,
            extraction_status="failed",
            gemini_consent_granted=True,
            gemini_consented_at=now,
            extracted_fields=None,
            field_provenance=None,
            stripped_ungrounded_fields=None,
            conflict_suggestions=None,
            missing_fields=deterministic_missing,
            error_message=exc.user_message,
            created_at=now,
        )
        db.add(failed_draft)
        case.updated_at = now
        _append_timeline_event(
            db,
            case_id=case.id,
            event_type="ai_extraction_failed",
            event_source="system",
            title=f"AI extraction failed for recording #{recording.recording_order}",
            notes=f"{exc.error_code}: {exc.user_message}",
            snapshot_payload={
                "draft_id": draft_id,
                "recording_id": recording.id,
                "error_code": exc.error_code,
            },
            occurred_at=now,
        )
        db.commit()
        db.refresh(failed_draft)
        raise

    try:
        extracted_model, grounded_prov, stripped_claims, llm_gaps = validate_and_ground_extraction(
            raw_payload=result.raw_payload,
            approved_transcript=approved_text,
            recording_id=recording.id,
            transcript_layer=transcript_layer,
        )
    except ValidationError as exc:
        raise LLMProviderError(
            error_code="schema_violation",
            user_message=f"Gemini output violated extraction schema: {exc.errors()[0]['msg']}",
            http_status=502,
            retryable=True,
        ) from exc

    extracted_dict = extracted_model.model_dump()
    existing_draft = dict(case.draft_details or PaymentDiscrepancyDetails().model_dump())
    existing_prov = dict(case.field_provenance or {})
    conflicts: dict[str, dict] = {}

    for field_name, new_val in extracted_dict.items():
        if new_val is None:
            continue
        new_prov = grounded_prov.get(field_name)
        existing_val = existing_draft.get(field_name)
        if existing_val is None:
            existing_draft[field_name] = new_val
            if new_prov is not None:
                existing_prov[field_name] = new_prov
        elif existing_val != new_val:
            conflicts[field_name] = {
                "field": field_name,
                "existing_value": existing_val,
                "existing_provenance": existing_prov.get(field_name),
                "proposed_value": new_val,
                "proposed_provenance": new_prov,
            }

    merged_model = PaymentDiscrepancyDetails.model_validate(existing_draft)
    combined_missing = detect_missing_payment_fields(merged_model)
    for gap_text in llm_gaps.get("missing_information", []):
        if not any(gap_text.lower() in item["prompt"].lower() for item in combined_missing):
            combined_missing.append(
                {
                    "field": "additional_gap",
                    "importance": "suggested",
                    "prompt": gap_text,
                }
            )
    for q_text in llm_gaps.get("suggested_followup_questions", []):
        if not any(q_text.lower() == item["prompt"].lower() for item in combined_missing):
            combined_missing.append(
                {
                    "field": "followup_question",
                    "importance": "followup",
                    "prompt": q_text,
                }
            )

    # Preserve confirmed cases safely: never overwrite confirmed_details; only merge into draft cases.
    if case.status == "draft":
        case.draft_details = merged_model.model_dump()
        case.field_provenance = existing_prov or None
        if case.platform == "other" and merged_model.platform is not None:
            case.platform = merged_model.platform
    case.updated_at = now

    draft = CaseExtractionDraft(
        id=draft_id,
        case_id=case.id,
        voice_recording_id=recording.id,
        source_transcript_layer=transcript_layer,
        source_transcript_snapshot=approved_text,
        llm_provider=result.provider,
        llm_model=result.model,
        extraction_status="completed",
        gemini_consent_granted=True,
        gemini_consented_at=now,
        extracted_fields=extracted_dict,
        field_provenance=grounded_prov or None,
        stripped_ungrounded_fields=stripped_claims or None,
        conflict_suggestions=conflicts or None,
        missing_fields=combined_missing,
        error_message=None,
        created_at=now,
    )
    db.add(draft)

    _append_timeline_event(
        db,
        case_id=case.id,
        event_type="ai_draft_extracted",
        event_source="system",
        title=f"Extracted draft fields from recording #{recording.recording_order} ({transcript_layer})",
        notes=(
            f"Grounded fields: {len(grounded_prov)}, "
            f"Rejected unsupported claims: {len(stripped_claims)}, "
            f"Conflicts flagged: {len(conflicts)}"
        ),
        snapshot_payload={
            "draft_id": draft_id,
            "recording_id": recording.id,
            "transcript_layer": transcript_layer,
            "grounded_count": len(grounded_prov),
            "stripped_count": len(stripped_claims),
            "conflict_count": len(conflicts),
        },
        occurred_at=now,
    )
    db.commit()
    db.refresh(draft)
    return draft
