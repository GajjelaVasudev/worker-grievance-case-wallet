"""Regression tests for Quote-to-Value Grounding Integrity Audit (Phase 3 Task 4)."""

import json
import subprocess

import httpx
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.adapters.gemini_llm import GeminiLLMProvider
from app.cases import service
from app.core.config import Settings
from app.core.database import Base, ensure_default_worker
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording  # noqa: F401
from app.models.worker import WorkerProfile  # noqa: F401
from app.routing.matcher import ensure_routing_seed
from app.schemas.case import ConfirmCaseRequest, CreateCaseRequest, PaymentDiscrepancyDetails
from app.storage.local_store import LocalStorageService
from app.voice.extraction import extract_draft_from_recording
from app.voice.storage import store_case_recording
from app.voice.transcription import save_worker_transcript_edit


def _make_fk_session_factory(db_path):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _make_wav_bytes(tmp_path, name: str = "audit.wav") -> bytes:
    target = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0", str(target)],
        check=True,
    )
    return target.read_bytes()


def _gemini_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "store"),
        asr_provider="none",
        sarvam_api_key="",
        llm_provider="gemini",
        gemini_api_key="AIzaSyAuditTestKeyOnly",
        gemini_model="gemini-3.6-flash",
    )


def _blank_payload() -> dict:
    null_entry = {"value": None, "source_quote": None}
    return {
        "platform": dict(null_entry),
        "grievance_category": dict(null_entry),
        "short_issue_summary": dict(null_entry),
        "disputed_amount": dict(null_entry),
        "currency": dict(null_entry),
        "relevant_period": dict(null_entry),
        "work_completed": dict(null_entry),
        "expected_payment_amount": dict(null_entry),
        "actual_payment_amount": dict(null_entry),
        "incentive_scheme_name": dict(null_entry),
        "work_city": dict(null_entry),
        "prior_ticket_id": dict(null_entry),
        "previous_support_contacts": dict(null_entry),
        "support_responses": dict(null_entry),
        "missing_information": [],
        "suggested_followup_questions": [],
    }


def _run_mocked_extract(tmp_path, *, transcript: str, llm_overrides: dict, raw_asr: str | None = None):
    payload = _blank_payload()
    payload.update(llm_overrides)
    envelope = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=envelope)))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "audit.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest())
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="clip.wav")
        if raw_asr is not None:
            rec.raw_transcript = raw_asr
            db.commit()
        save_worker_transcript_edit(db, case=case, recording=rec, edited_transcript=transcript)

        draft = extract_draft_from_recording(
            db,
            case=case,
            recording=rec,
            settings=settings,
            consent_granted=True,
            transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript",
            llm_provider=provider,
        )
        db.refresh(case)
        db.refresh(rec)
        return service.serialize_case(db, case), draft.id, rec.raw_transcript, rec.worker_edited_transcript


def test_850_quote_with_wrong_8500_value_rejected_and_clarification_added(tmp_path):
    """Exact ₹850 quote must reject extracted 8500, keep null, preserve reason, and flag clarification."""
    transcript = "My weekly payment is ₹850 less than expected."
    case_data, _, raw_asr, edited = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        raw_asr="Raw ASR immutable copy",
        llm_overrides={
            "disputed_amount": {"value": 8500, "source_quote": "My weekly payment is ₹850 less than expected."},
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["extracted_fields"]["discrepancy_amount_inr"] is None
    assert case_data["draft_details"]["discrepancy_amount_inr"] is None
    assert "discrepancy_amount_inr" in draft["stripped_ungrounded_fields"]
    reason = draft["stripped_ungrounded_fields"]["discrepancy_amount_inr"]["reason"]
    assert "8500" in reason
    assert any(
        m["field"] == "discrepancy_amount_inr" and m["importance"] == "clarification_required"
        for m in case_data["missing_fields"]
    )
    assert raw_asr == "Raw ASR immutable copy"
    assert edited == transcript
    assert case_data["status"] == "draft"
    assert case_data["confirmed_details"] is None


def test_850_quote_with_matching_850_value_accepted(tmp_path):
    """Exact ₹850 quote with extracted value 850 passes validation with full provenance."""
    transcript = "My weekly payment is ₹850 less than expected."
    case_data, _, _, _ = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        llm_overrides={
            "disputed_amount": {"value": 850, "source_quote": "My weekly payment is ₹850 less than expected."},
            "currency": {"value": "INR", "source_quote": "₹850"},
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["extracted_fields"]["discrepancy_amount_inr"] == 850.0
    assert draft["extracted_fields"]["currency"] == "INR"
    assert draft["stripped_ungrounded_fields"] is None
    assert draft["field_provenance"]["discrepancy_amount_inr"]["source_quote"] == transcript
    assert case_data["status"] == "draft"


def test_missing_currency_in_quote_rejects_inferred_currency(tmp_path):
    """If quote says 'missing 850' without currency marker, inferred 'INR' is rejected to null."""
    transcript = "My weekly payment is missing 850."
    case_data, _, _, _ = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        llm_overrides={
            "disputed_amount": {"value": 850, "source_quote": "missing 850"},
            "currency": {"value": "INR", "source_quote": "missing 850"},
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["extracted_fields"]["discrepancy_amount_inr"] == 850.0
    assert draft["extracted_fields"]["currency"] is None
    assert "currency" in draft["stripped_ungrounded_fields"]


def test_fabricated_platform_rejected(tmp_path):
    """Extracting 'zomato' from a Swiggy quote or platform-less quote is rejected to null."""
    transcript = "My Swiggy weekly payout is short."
    case_data, _, _, _ = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        llm_overrides={
            "platform": {"value": "zomato", "source_quote": "My Swiggy weekly payout is short."},
            "short_issue_summary": {"value": "Zomato payout short", "source_quote": "My Swiggy weekly payout is short."},
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["extracted_fields"]["platform"] is None
    assert draft["extracted_fields"]["issue_description"] is None
    assert "platform" in draft["stripped_ungrounded_fields"]
    assert "issue_description" in draft["stripped_ungrounded_fields"]


def test_contradictory_or_invented_date_period_rejected(tmp_path):
    """Inventing 'November 10-17, 2026' from 'last week' or wrong month 'October 18' from 'November 18' fails."""
    transcript = "My payout from last week and November 18 is missing."
    case_data, _, _, _ = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        llm_overrides={
            "relevant_period": {"value": "November 10-17, 2026", "source_quote": "last week"},
            "expected_payment_amount": {"value": 500, "source_quote": "November 18"},
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["extracted_fields"]["payout_cycle_raw"] is None
    assert "payout_cycle_raw" in draft["stripped_ungrounded_fields"]
    # Also verify non-matching numeric value in date quote does not pass as amount (18 != 500)
    assert draft["extracted_fields"]["expected_amount_inr"] is None
    assert "expected_amount_inr" in draft["stripped_ungrounded_fields"]


def test_fabricated_quotation_rejected(tmp_path):
    """A quotation not verbatim in the approved transcript is rejected completely."""
    transcript = "My Rapido incentive was not paid."
    case_data, _, _, _ = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        llm_overrides={
            "expected_payment_amount": {"value": 850, "source_quote": "I was promised 850 bonus"},
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["extracted_fields"]["expected_amount_inr"] is None
    assert "expected_amount_inr" in draft["stripped_ungrounded_fields"]


def test_valid_summary_supported_by_exact_quotation_accepted(tmp_path):
    """A concise summary lexically supported by an exact transcript quotation passes."""
    transcript = "My Swiggy weekly payout is missing ₹850 after completing all eligible orders."
    case_data, _, _, _ = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        llm_overrides={
            "platform": {"value": "swiggy", "source_quote": "Swiggy"},
            "short_issue_summary": {
                "value": "Swiggy weekly payout missing ₹850",
                "source_quote": "My Swiggy weekly payout is missing ₹850",
            },
            "work_completed": {
                "value": "completed all eligible orders",
                "source_quote": "completing all eligible orders",
            },
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["extracted_fields"]["issue_description"] == "Swiggy weekly payout missing ₹850"
    assert draft["extracted_fields"]["work_completed_summary"] == "completed all eligible orders"
    assert draft["stripped_ungrounded_fields"] is None


def test_valid_hinglish_code_mixed_transcript_accepted(tmp_path):
    """Code-mixed Hinglish transcript grounds period, work completed, category, and support contact."""
    transcript = (
        "मैं delivery partner हूँ। मैंने इस week सारे orders complete किए लेकिन मेरा incentive अभी तक "
        "credit नहीं हुआ। support team को भी 2 बार contact किया है।"
    )
    case_data, _, _, _ = _run_mocked_extract(
        tmp_path,
        transcript=transcript,
        llm_overrides={
            "grievance_category": {
                "value": "payment_discrepancy",
                "source_quote": "मेरा incentive अभी तक credit नहीं हुआ",
            },
            "short_issue_summary": {
                "value": "Incentive not credited after completing orders this week",
                "source_quote": "मैंने इस week सारे orders complete किए लेकिन मेरा incentive अभी तक credit नहीं हुआ",
            },
            "relevant_period": {"value": "this week", "source_quote": "इस week"},
            "work_completed": {
                "value": "completed all orders this week",
                "source_quote": "इस week सारे orders complete किए",
            },
            "previous_support_contacts": {
                "value": "contacted support team 2 times",
                "source_quote": "support team को भी 2 बार contact किया है",
            },
        },
    )
    draft = case_data["extraction_drafts"][0]
    assert draft["stripped_ungrounded_fields"] is None
    assert draft["extracted_fields"]["payout_cycle_raw"] == "this week"
    assert draft["extracted_fields"]["work_completed_summary"] == "completed all orders this week"
    assert draft["extracted_fields"]["support_contact_summary"] == "contacted support team 2 times"
    assert draft["field_provenance"]["grievance_category"]["source_quote"] == "मेरा incentive अभी तक credit नहीं हुआ"


def test_multiple_recordings_conflicting_amounts_preserved_for_confirmation(tmp_path):
    """Conflicting amounts across two recordings surface in conflicts and require explicit worker confirmation."""
    responses = [
        {
            **_blank_payload(),
            "platform": {"value": "swiggy", "source_quote": "Swiggy"},
            "disputed_amount": {"value": 850, "source_quote": "missing ₹850"},
            "short_issue_summary": {"value": "Swiggy payout missing ₹850", "source_quote": "Swiggy payout missing ₹850"},
        },
        {
            **_blank_payload(),
            "disputed_amount": {"value": 1200, "source_quote": "actually missing ₹1200"},
        },
    ]
    idx = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal idx
        body = responses[idx]
        idx += 1
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]})

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t4_conflict.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest())
        rec1 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r1.wav")
        rec2 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r2.wav")
        save_worker_transcript_edit(db, case=case, recording=rec1, edited_transcript="Swiggy payout missing ₹850.")
        save_worker_transcript_edit(db, case=case, recording=rec2, edited_transcript="Update: actually missing ₹1200.")

        extract_draft_from_recording(
            db, case=case, recording=rec1, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )
        d2 = extract_draft_from_recording(
            db, case=case, recording=rec2, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )

        db.refresh(case)
        assert case.draft_details["discrepancy_amount_inr"] == 850.0
        assert case.status == "draft"
        assert case.confirmed_details is None
        assert "discrepancy_amount_inr" in d2.conflict_suggestions
        conflict = d2.conflict_suggestions["discrepancy_amount_inr"]
        assert conflict["existing_value"] == 850.0
        assert conflict["existing_provenance"]["voice_recording_id"] == rec1.id
        assert conflict["proposed_value"] == 1200.0
        assert conflict["proposed_provenance"]["voice_recording_id"] == rec2.id

        confirmed = service.confirm_case(
            db,
            case=case,
            payload=ConfirmCaseRequest(
                confirm=True,
                platform="swiggy",
                details=PaymentDiscrepancyDetails(
                    platform="swiggy",
                    discrepancy_amount_inr=1200.0,
                    issue_description="Swiggy payout missing ₹1200 confirmed after checking statement",
                ),
            ),
        )
        assert confirmed.status == "confirmed"
        assert confirmed.confirmed_details["discrepancy_amount_inr"] == 1200.0
